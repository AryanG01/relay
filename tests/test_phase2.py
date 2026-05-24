"""
Phase 2 tests — LLM pipeline (all LLM calls mocked).

Tests:
  1. content_hash is deterministic and non-empty
  2. _strip_fences removes markdown code fences correctly
  3. parse_jd returns ParsedJD and stores result in DB
  4. parse_jd returns cached result on second call (no second LLM call)
  5. match_scorer pre-filter returns score=0 on < 25% keyword overlap
  6. match_scorer returns weighted score >= 70 when skills match well
  7. gap_analyzer identifies covered vs hard_gap skills
  8. select_bullets returns <=  max_bullets_per_role bullets per role
  9. keyword_injector leaves bullet unchanged when LLM returns SKIP
  10. resume_renderer produces a non-empty file on disk
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_parsed_jd():
    from backend.schemas import ParsedJD
    return ParsedJD(
        required_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "microservices"],
        preferred_skills=["Kubernetes", "Redis"],
        responsibilities=["Build REST APIs", "Design microservices", "Write tests"],
        tech_stack=["Python", "FastAPI", "PostgreSQL", "Docker"],
        role_level="mid",
        domain="software",
        remote_type="hybrid",
        confidence=0.92,
        raw_keywords=["Python", "FastAPI", "PostgreSQL", "Docker"],
    )


@pytest.fixture
def sample_resume():
    from backend.schemas import MasterResume
    with open("data/master_resume.json") as f:
        return MasterResume.model_validate(json.load(f))


@pytest.fixture
def sample_match_result():
    from backend.schemas import MatchResult
    return MatchResult(
        overall_score=78.5,
        required_coverage=0.85,
        experience_relevance=0.78,
        domain_alignment=0.75,
        seniority_fit=0.90,
        missing_required=["Kubernetes"],
        strong_matches=["Python", "FastAPI", "Docker"],
    )


# ---------------------------------------------------------------------------
# 1–2: Utils
# ---------------------------------------------------------------------------

def test_content_hash_deterministic():
    from backend.utils.hashing import content_hash
    assert content_hash("hello") == content_hash("hello")
    assert len(content_hash("hello")) == 64
    assert content_hash("a") != content_hash("b")


def test_strip_fences_removes_markdown():
    from backend.utils.llm import _strip_fences
    assert _strip_fences("```json\n{\"k\": 1}\n```") == '{"k": 1}'
    assert _strip_fences('{"k": 1}') == '{"k": 1}'
    assert _strip_fences("```\nhello\n```") == "hello"


# ---------------------------------------------------------------------------
# 3–4: JD Parser
# ---------------------------------------------------------------------------

_FAKE_JD_JSON = json.dumps({
    "required_skills": ["Python", "FastAPI"],
    "preferred_skills": ["Docker"],
    "responsibilities": ["Build REST APIs"],
    "tech_stack": ["Python", "FastAPI"],
    "role_level": "mid",
    "domain": "software",
    "years_experience_min": 2,
    "years_experience_max": 4,
    "culture_signals": [],
    "red_flags": [],
    "sponsorship_available": None,
    "remote_type": "hybrid",
    "confidence": 0.9,
    "raw_keywords": ["Python", "FastAPI"],
})


@pytest.mark.asyncio
async def test_parse_jd_returns_parsed_jd():
    from backend.database import AsyncSessionLocal, init_db
    from backend.services.jd_parser import parse_jd

    await init_db()
    with patch("backend.services.jd_parser.call_llm", new=AsyncMock(return_value=_FAKE_JD_JSON)):
        async with AsyncSessionLocal() as session:
            result = await parse_jd("Python FastAPI backend engineer role", session)

    assert result.role_level == "mid"
    assert "Python" in result.required_skills
    assert result.confidence == 0.9


@pytest.mark.asyncio
async def test_parse_jd_uses_cache_on_second_call():
    """LLM must be called only once for the same JD text."""
    from backend.database import AsyncSessionLocal, init_db
    from backend.services.jd_parser import parse_jd

    await init_db()
    mock_llm = AsyncMock(return_value=_FAKE_JD_JSON)
    jd_text = "Unique JD text for cache test 12345"
    with patch("backend.services.jd_parser.call_llm", new=mock_llm):
        async with AsyncSessionLocal() as session:
            await parse_jd(jd_text, session)
            await parse_jd(jd_text, session)

    assert mock_llm.call_count == 1


# ---------------------------------------------------------------------------
# 5–6: Match Scorer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_match_scorer_prefilter_zero_score(sample_resume):
    """Completely irrelevant JD → score=0, no LLM call."""
    from backend.schemas import ParsedJD
    from backend.services.match_scorer import score_match

    jd = ParsedJD(
        required_skills=["COBOL", "Fortran", "RPG", "CICS", "AS400"],
        preferred_skills=[],
        responsibilities=["Maintain mainframe systems"],
        tech_stack=["COBOL", "Fortran"],
        role_level="senior",
        domain="other",
        confidence=0.8,
        raw_keywords=["COBOL"],
    )
    mock_llm = AsyncMock()
    with patch("backend.services.match_scorer.call_llm", new=mock_llm):
        result = await score_match(sample_resume, jd)

    assert result.overall_score == 0.0
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_match_scorer_high_score_on_good_match(sample_resume, sample_parsed_jd):
    """Python/FastAPI JD against Aryan's resume should score >= 70."""
    semantic = json.dumps({"experience_relevance": 0.85, "domain_alignment": 0.80, "reasoning": "Good match"})
    with patch("backend.services.match_scorer.call_llm", new=AsyncMock(return_value=semantic)):
        from backend.services.match_scorer import score_match
        result = await score_match(sample_resume, sample_parsed_jd)

    assert result.overall_score >= 70.0
    assert len(result.strong_matches) > 0


# ---------------------------------------------------------------------------
# 7: Gap Analyzer
# ---------------------------------------------------------------------------

def test_gap_analyzer_covered_and_missing(sample_resume, sample_parsed_jd):
    from backend.services.gap_analyzer import analyze_gaps

    report = analyze_gaps(sample_resume, sample_parsed_jd)
    assert "Python" in report.covered
    assert isinstance(report.hard_gaps, list)
    assert isinstance(report.soft_gaps, list)


# ---------------------------------------------------------------------------
# 8: Bullet Selector
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_select_bullets_max_per_role(sample_resume, sample_parsed_jd, sample_match_result):
    """Each role must have at most max_bullets_per_role bullets."""
    from backend.services.bullet_selector import select_bullets

    # Mock returns a score of 0.7 for every bullet id it receives
    async def _mock_llm(prompt: str, **kw):
        # Extract bullet ids from the JSON array in the prompt
        try:
            bullets_part = prompt.split("Bullets to score:\n")[1].strip()
            bullets = json.loads(bullets_part)
            return json.dumps([{"id": b["id"], "score": 0.7} for b in bullets])
        except Exception:
            return json.dumps([])

    with patch("backend.services.bullet_selector.call_llm", new=AsyncMock(side_effect=_mock_llm)):
        result = await select_bullets(sample_resume, sample_parsed_jd, sample_match_result, max_bullets_per_role=2)

    for exp in result.work_experience:
        assert len(exp.get("bullets", [])) <= 2


# ---------------------------------------------------------------------------
# 9: Keyword Injector
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_keyword_injector_skip_leaves_bullet_unchanged(sample_parsed_jd):
    """When LLM returns SKIP the original bullet text must be preserved."""
    from backend.schemas import SelectedResume
    from backend.services.keyword_injector import inject_keywords

    sel = SelectedResume(
        personal={"name": "Test User"},
        work_experience=[{
            "role": "Engineer", "company": "Acme", "location": "SG",
            "start_date": "2024-01", "end_date": "2025-01",
            "bullets": [{"id": "b1", "text": "Built REST APIs with Python", "skills": ["Python"]}],
        }],
        section_order=["work_experience", "projects", "skills", "education"],
    )
    sample_parsed_jd.required_skills = ["Kubernetes"]  # missing from bullet text

    with patch("backend.services.keyword_injector.call_llm", new=AsyncMock(return_value="SKIP")):
        result = await inject_keywords(sel, sample_parsed_jd)

    assert result.work_experience[0]["bullets"][0]["text"] == "Built REST APIs with Python"


# ---------------------------------------------------------------------------
# 10: Resume Renderer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_renderer_produces_file(sample_resume, tmp_path, monkeypatch):
    """render_resume must write a non-empty file to disk."""
    from backend.database import AsyncSessionLocal, init_db
    from backend.schemas import SelectedResume
    from backend.services import resume_renderer

    monkeypatch.setattr(resume_renderer, "RESUMES_DIR", tmp_path)
    await init_db()

    sel = SelectedResume(
        personal=sample_resume.personal.model_dump(),
        summary=sample_resume.summary,
        work_experience=[e.model_dump() for e in sample_resume.work_experience[:1]],
        education=[e.model_dump() for e in sample_resume.education],
        skills=sample_resume.skills.model_dump(),
        projects=[p.model_dump() for p in sample_resume.projects[:1]],
        certifications=[],
    )

    async with AsyncSessionLocal() as session:
        path = await resume_renderer.render_resume(sel, "test-app-001", session)

    assert os.path.exists(path), f"File not found: {path}"
    assert os.path.getsize(path) > 0, "Rendered file is empty"
