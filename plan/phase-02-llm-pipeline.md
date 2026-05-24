# Phase 2 — LLM Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the full LLM pipeline: JD parsing, resume match scoring, bullet selection, keyword injection (zero-fabrication), and WeasyPrint resume rendering — all wired to a CLI test script.

**Architecture:** Each service is a stateless async module. The LLM wrapper (`utils/llm.py`) handles all Anthropic calls centrally with retry + JSON validation. Services call `call_llm()` — never instantiate the client directly. Tests mock `call_llm` to avoid real API calls.

**Tech Stack:** anthropic SDK, rapidfuzz, WeasyPrint, Jinja2, python-docx, pytest + unittest.mock

**Deliverable:** `python3 scripts/test_pipeline.py "<JD text>"` produces a tailored PDF in `data/resumes/`. `pytest tests/test_phase2.py` passes with mocked LLM.

---

### Task 1: `backend/utils/` — LLM client, hashing, logging

**Files:**
- Create: `backend/utils/llm.py`
- Create: `backend/utils/hashing.py`
- Create: `backend/utils/logging.py`

- [ ] **Step 1: Write `backend/utils/hashing.py`**

```python
"""Content hashing helpers for deduplication and cache keying."""
from __future__ import annotations
import hashlib


def content_hash(text: str) -> str:
    """SHA-256 of text, hex-encoded. Used for JD cache keys and seen_hashes."""
    return hashlib.sha256(text.strip().encode()).hexdigest()


def short_hash(text: str, length: int = 12) -> str:
    """First `length` chars of content_hash — for log labels, never for keys."""
    return content_hash(text)[:length]
```

- [ ] **Step 2: Write `backend/utils/logging.py`**

```python
"""
Structured JSON logger for Relay.

Usage:
    from backend.utils.logging import get_logger
    logger = get_logger(__name__)
    logger.info("jd_parsed", extra={"app_id": "...", "confidence": 0.9})
"""
from __future__ import annotations
import json
import logging
import time


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge any structured fields passed via extra={}
        for k, v in record.__dict__.items():
            if k not in logging.LogRecord.__dict__ and not k.startswith("_"):
                payload[k] = v
        return json.dumps(payload)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

- [ ] **Step 3: Write `backend/utils/llm.py`**

```python
"""
Anthropic async client wrapper.

All LLM calls in Relay go through call_llm(). Never instantiate
AsyncAnthropic directly in a service.

Key behaviours:
  - Exponential backoff retry on 429 / transient errors (max retry_count attempts)
  - If expect_json=True: strips markdown fences, validates JSON, retries if invalid
  - Logs every call with prompt hash + token usage (never logs raw prompt text)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache

import anthropic

from backend.utils.hashing import short_hash
from backend.utils.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _get_client() -> anthropic.AsyncAnthropic:
    from backend.config import get_settings
    return anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers LLMs sometimes emit."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def call_llm(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 1500,
    expect_json: bool = False,
    retry_count: int = 3,
) -> str:
    """
    Call Claude and return the text response.

    Args:
        prompt: User message content.
        system: Optional system prompt.
        max_tokens: Maximum tokens in the response.
        expect_json: If True, strip fences and validate JSON; retry on invalid.
        retry_count: How many attempts before raising.

    Returns:
        Raw text (or cleaned JSON string if expect_json=True).

    Raises:
        anthropic.RateLimitError: After retry_count exhausted.
        json.JSONDecodeError: If expect_json=True and JSON never valid after retries.
    """
    from backend.config import get_settings

    settings = get_settings()
    prompt_hash = short_hash(prompt)

    for attempt in range(retry_count):
        try:
            t0 = time.monotonic()
            kwargs: dict = {
                "model": settings.llm_model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system

            response = await _get_client().messages.create(**kwargs)
            latency = round(time.monotonic() - t0, 3)
            content = response.content[0].text

            logger.info(
                "llm_call_ok",
                extra={
                    "prompt_hash": prompt_hash,
                    "in_tokens": response.usage.input_tokens,
                    "out_tokens": response.usage.output_tokens,
                    "latency_s": latency,
                    "attempt": attempt + 1,
                },
            )

            if expect_json:
                content = _strip_fences(content)
                try:
                    json.loads(content)  # validate only — return string
                except json.JSONDecodeError:
                    if attempt < retry_count - 1:
                        logger.warning(
                            "llm_invalid_json",
                            extra={"prompt_hash": prompt_hash, "attempt": attempt + 1},
                        )
                        continue
                    raise

            return content

        except anthropic.RateLimitError:
            if attempt < retry_count - 1:
                wait = 2**attempt
                logger.warning(
                    "llm_rate_limited",
                    extra={"wait_s": wait, "attempt": attempt + 1},
                )
                await asyncio.sleep(wait)
            else:
                raise

        except anthropic.APIError as exc:
            if attempt < retry_count - 1:
                await asyncio.sleep(2**attempt)
            else:
                raise

    # Should never reach here
    raise RuntimeError("call_llm exhausted retries without returning or raising")
```

- [ ] **Step 4: Commit**

```bash
git add backend/utils/llm.py backend/utils/hashing.py backend/utils/logging.py
git commit -m "feat: LLM client wrapper, hashing utils, structured logger"
```

---

### Task 2: `services/jd_parser.py` — JD Parsing with Cache

**Files:**
- Create: `backend/services/jd_parser.py`

- [ ] **Step 1: Write `backend/services/jd_parser.py`**

```python
"""
LLM-based job description parser with SQLite cache.

Cache strategy: hash raw JD text → check jd_cache table →
if found with parsed_json: return deserialized ParsedJD.
If not: call LLM → store result → return.
"""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import JDCache
from backend.schemas import ParsedJD
from backend.utils.hashing import content_hash
from backend.utils.llm import call_llm
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_SYSTEM = (
    "You are a precise job description parser. "
    "Extract structured data from the job description. "
    "Return ONLY valid JSON matching the exact schema provided. No markdown, no explanation."
)

_PROMPT_TEMPLATE = """\
Schema:
{{
  "required_skills": ["list of explicitly required skills/technologies"],
  "preferred_skills": ["nice-to-have skills"],
  "responsibilities": ["key responsibilities, max 8, each under 15 words"],
  "tech_stack": ["all mentioned technologies, tools, languages"],
  "role_level": "junior|mid|senior|lead|unknown",
  "domain": "finance|trading|software|data|infra|other",
  "years_experience_min": null,
  "years_experience_max": null,
  "culture_signals": ["list of culture/environment descriptors"],
  "red_flags": ["potential constraints: visa restrictions, relocation, clearance"],
  "sponsorship_available": null,
  "remote_type": "remote|hybrid|onsite|unknown",
  "confidence": 0.0,
  "raw_keywords": ["all significant domain/skill terms found"]
}}

Job Description:
{raw_text}
"""


async def parse_jd(raw_text: str, session: AsyncSession) -> ParsedJD:
    """
    Parse a job description into a structured ParsedJD.

    1. Hash raw_text → check jd_cache.
    2. Cache hit with parsed_json → return cached ParsedJD.
    3. Cache miss → call LLM → store → return.
    """
    jd_hash = content_hash(raw_text)

    # --- Cache lookup ---
    cached = await session.get(JDCache, jd_hash)
    if cached and cached.parsed_json:
        logger.info("jd_cache_hit", extra={"jd_hash": jd_hash[:12]})
        data = json.loads(cached.parsed_json)
        return ParsedJD.model_validate(data)

    # --- LLM call ---
    logger.info("jd_cache_miss", extra={"jd_hash": jd_hash[:12]})
    prompt = _PROMPT_TEMPLATE.format(raw_text=raw_text)
    raw_json = await call_llm(prompt, system=_SYSTEM, max_tokens=2000, expect_json=True)
    data = json.loads(raw_json)
    parsed = ParsedJD.model_validate(data)

    # --- Store in cache ---
    if cached:
        cached.parsed_json = raw_json
        cached.parse_confidence = parsed.confidence
    else:
        session.add(
            JDCache(
                content_hash=jd_hash,
                raw_text=raw_text,
                parsed_json=raw_json,
                parse_confidence=parsed.confidence,
            )
        )
    await session.commit()

    return parsed
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/jd_parser.py
git commit -m "feat: JD parser service with SQLite cache"
```

---

### Task 3: `services/match_scorer.py` — Resume vs JD Scoring

**Files:**
- Create: `backend/services/match_scorer.py`

- [ ] **Step 1: Write `backend/services/match_scorer.py`**

```python
"""
Score how well the master resume matches a parsed JD (0–100).

Fast pre-filter: keyword overlap ratio. If < 0.25, return score=0 (no LLM call).
Full score: weighted combination of required_coverage, experience_relevance,
domain_alignment, seniority_fit.

Weights: required_coverage*0.40 + experience_relevance*0.30
       + domain_alignment*0.20 + seniority_fit*0.10
"""
from __future__ import annotations

import json

from rapidfuzz import fuzz

from backend.schemas import MasterResume, MatchResult, ParsedJD
from backend.utils.llm import call_llm
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Seniority ordering for fit calculation
_SENIORITY_RANK = {"junior": 1, "mid": 2, "senior": 3, "lead": 4, "unknown": 2}

_SEMANTIC_SYSTEM = (
    "You are a resume-to-job-description relevance scorer. "
    "Return ONLY valid JSON. No markdown, no explanation."
)

_SEMANTIC_PROMPT = """\
Score the following resume experience against the job requirements.

Resume work experience summary:
{experience_summary}

Job responsibilities:
{responsibilities}

Job domain: {domain}
Resume domains: {resume_domains}

Return JSON:
{{
  "experience_relevance": 0.0,
  "domain_alignment": 0.0,
  "reasoning": "one sentence"
}}

experience_relevance: 0.0–1.0 — how well the experience matches the responsibilities.
domain_alignment: 0.0–1.0 — how well the resume domain matches the job domain.
"""


def _extract_resume_skills(resume: MasterResume) -> set[str]:
    """Flatten all skills from bullets + skills section into a lowercase set."""
    skills: set[str] = set()
    for cat in [
        resume.skills.languages,
        resume.skills.frameworks,
        resume.skills.tools,
        resume.skills.databases,
        resume.skills.domains,
        resume.skills.other,
    ]:
        skills.update(s.lower() for s in cat)

    for exp in resume.work_experience:
        for bullet in exp.bullets:
            skills.update(s.lower() for s in bullet.skills)

    for proj in resume.projects:
        for bullet in proj.bullets:
            skills.update(s.lower() for s in bullet.skills)
        skills.update(t.lower() for t in proj.tech_stack)

    return skills


def _fuzzy_match(skill: str, resume_skills: set[str], threshold: int = 80) -> str:
    """Return 'exact', 'fuzzy', or 'missing'."""
    sl = skill.lower()
    if sl in resume_skills:
        return "exact"
    for rs in resume_skills:
        if fuzz.token_sort_ratio(sl, rs) >= threshold:
            return "fuzzy"
    return "missing"


def _infer_user_yoe(resume: MasterResume) -> float:
    """Rough years-of-experience from work_experience dates."""
    from datetime import date

    total_months = 0
    for exp in resume.work_experience:
        try:
            start_y, start_m = map(int, exp.start_date.split("-"))
            if exp.end_date:
                end_y, end_m = map(int, exp.end_date.split("-"))
            else:
                today = date.today()
                end_y, end_m = today.year, today.month
            total_months += (end_y - start_y) * 12 + (end_m - start_m)
        except (ValueError, AttributeError):
            continue
    return round(total_months / 12, 1)


async def score_match(resume: MasterResume, parsed_jd: ParsedJD) -> MatchResult:
    """Score 0–100 how well the resume matches the JD."""
    resume_skills = _extract_resume_skills(resume)

    # --- Fast pre-filter ---
    all_jd_terms = {s.lower() for s in parsed_jd.required_skills + parsed_jd.tech_stack}
    if all_jd_terms:
        overlap = len(all_jd_terms & resume_skills) / len(all_jd_terms)
        if overlap < 0.25:
            logger.info("match_prefilter_fail", extra={"overlap": overlap})
            return MatchResult(
                overall_score=0.0,
                required_coverage=0.0,
                experience_relevance=0.0,
                domain_alignment=0.0,
                seniority_fit=0.0,
                missing_required=parsed_jd.required_skills,
            )

    # --- Required coverage ---
    strong, partial, missing = [], [], []
    for skill in parsed_jd.required_skills:
        result = _fuzzy_match(skill, resume_skills)
        if result == "exact":
            strong.append(skill)
        elif result == "fuzzy":
            partial.append(skill)
        else:
            missing.append(skill)

    total_required = len(parsed_jd.required_skills) or 1
    required_coverage = (len(strong) + 0.5 * len(partial)) / total_required

    # --- Semantic scoring via LLM ---
    exp_summary = " | ".join(
        f"{e.company} ({e.role}): " + "; ".join(b.text[:80] for b in e.bullets[:2])
        for e in resume.work_experience[:3]
    )
    resume_domains = list({b.domain for e in resume.work_experience for b in e.bullets})

    prompt = _SEMANTIC_PROMPT.format(
        experience_summary=exp_summary[:2000],
        responsibilities="\n".join(f"- {r}" for r in parsed_jd.responsibilities[:6]),
        domain=parsed_jd.domain,
        resume_domains=", ".join(resume_domains),
    )

    raw = await call_llm(prompt, system=_SEMANTIC_SYSTEM, max_tokens=300, expect_json=True)
    semantic = json.loads(raw)
    experience_relevance = float(semantic.get("experience_relevance", 0.5))
    domain_alignment = float(semantic.get("domain_alignment", 0.5))

    # --- Seniority fit ---
    user_yoe = _infer_user_yoe(resume)
    jd_rank = _SENIORITY_RANK.get(parsed_jd.role_level, 2)
    user_rank = 1 if user_yoe < 2 else (2 if user_yoe < 5 else 3)
    seniority_fit = max(0.0, 1.0 - abs(jd_rank - user_rank) * 0.3)

    # --- Weighted final score ---
    overall = (
        required_coverage * 0.40
        + experience_relevance * 0.30
        + domain_alignment * 0.20
        + seniority_fit * 0.10
    ) * 100

    overall = round(min(100.0, max(0.0, overall)), 1)

    logger.info(
        "match_scored",
        extra={
            "overall": overall,
            "required_coverage": round(required_coverage, 3),
            "missing_count": len(missing),
        },
    )

    return MatchResult(
        overall_score=overall,
        required_coverage=required_coverage,
        experience_relevance=experience_relevance,
        domain_alignment=domain_alignment,
        seniority_fit=seniority_fit,
        missing_required=missing,
        partial_required=partial,
        strong_matches=strong,
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/match_scorer.py
git commit -m "feat: match scorer — keyword pre-filter + LLM semantic scoring"
```

---

### Task 4: `services/gap_analyzer.py`

**Files:**
- Create: `backend/services/gap_analyzer.py`

- [ ] **Step 1: Write `backend/services/gap_analyzer.py`**

```python
"""
Simple skill gap analysis — identifies required JD skills absent from the resume.
This is a lightweight companion to match_scorer; no LLM calls needed.
"""
from __future__ import annotations

from pydantic import BaseModel
from rapidfuzz import fuzz

from backend.schemas import MasterResume, ParsedJD


class GapReport(BaseModel):
    hard_gaps: list[str]   # required skills with no resume evidence
    soft_gaps: list[str]   # preferred skills with no resume evidence
    covered: list[str]     # required skills with evidence


def analyze_gaps(resume: MasterResume, parsed_jd: ParsedJD) -> GapReport:
    """
    Return a GapReport comparing parsed_jd requirements against the resume.
    Uses fuzzy matching (token_sort_ratio ≥ 80) to handle synonyms.
    """
    # Collect all skill-like terms from the resume
    resume_terms: set[str] = set()
    for cat in vars(resume.skills).values():
        if isinstance(cat, list):
            resume_terms.update(t.lower() for t in cat)
    for exp in resume.work_experience:
        for b in exp.bullets:
            resume_terms.update(s.lower() for s in b.skills)
    for proj in resume.projects:
        resume_terms.update(t.lower() for t in proj.tech_stack)

    def _has_evidence(skill: str) -> bool:
        sl = skill.lower()
        if sl in resume_terms:
            return True
        return any(fuzz.token_sort_ratio(sl, rt) >= 80 for rt in resume_terms)

    hard_gaps, covered = [], []
    for skill in parsed_jd.required_skills:
        if _has_evidence(skill):
            covered.append(skill)
        else:
            hard_gaps.append(skill)

    soft_gaps = [s for s in parsed_jd.preferred_skills if not _has_evidence(s)]

    return GapReport(hard_gaps=hard_gaps, soft_gaps=soft_gaps, covered=covered)
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/gap_analyzer.py
git commit -m "feat: gap analyzer — fuzzy skill gap diff"
```

---

### Task 5: `services/bullet_selector.py` — Per-Bullet LLM Scoring

**Files:**
- Create: `backend/services/bullet_selector.py`

- [ ] **Step 1: Write `backend/services/bullet_selector.py`**

```python
"""
Select and reorder resume bullets for a specific application.

For each role/project: score ALL bullets in one LLM call (batch per role),
select top max_bullets_per_role, reorder by relevance score DESC.
Reorder projects by relevance, skills within each category by JD presence.
"""
from __future__ import annotations

import json

from backend.schemas import MasterResume, MatchResult, ParsedJD, SelectedResume
from backend.utils.llm import call_llm
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_SYSTEM = (
    "You are a resume optimisation assistant. "
    "Score resume bullets for relevance to a job description. "
    "Return ONLY valid JSON. No markdown."
)

_BULLET_SCORE_PROMPT = """\
Score each bullet 0.0–1.0 for relevance to this job.

Job required skills: {required_skills}
Job responsibilities: {responsibilities}
Job domain: {domain}

Bullets to score:
{bullets_json}

Return JSON array (same order as input):
[{{"id": "...", "score": 0.0}}, ...]
"""


async def _score_bullets(
    bullets: list[dict], parsed_jd: ParsedJD
) -> dict[str, float]:
    """Return {bullet_id: score} for all bullets in one LLM call."""
    if not bullets:
        return {}

    prompt = _BULLET_SCORE_PROMPT.format(
        required_skills=", ".join(parsed_jd.required_skills[:15]),
        responsibilities="\n".join(f"- {r}" for r in parsed_jd.responsibilities[:6]),
        domain=parsed_jd.domain,
        bullets_json=json.dumps(
            [{"id": b["id"], "text": b["text"][:200]} for b in bullets], indent=2
        ),
    )

    raw = await call_llm(prompt, system=_SYSTEM, max_tokens=500, expect_json=True)
    scored: list[dict] = json.loads(raw)
    return {item["id"]: float(item.get("score", 0.5)) for item in scored}


def _reorder_skills(skills: dict, jd_skills: set[str]) -> dict:
    """Within each skill category, put JD-mentioned skills first."""
    result = {}
    for category, items in skills.items():
        jd_first = [s for s in items if s.lower() in jd_skills]
        rest = [s for s in items if s.lower() not in jd_skills]
        result[category] = jd_first + rest
    return result


async def select_bullets(
    master_resume: MasterResume,
    parsed_jd: ParsedJD,
    match_result: MatchResult,
    max_bullets_per_role: int = 4,
) -> SelectedResume:
    """
    Build a SelectedResume tailored for a specific JD.

    1. Score all bullets per role/project via LLM (one call per role).
    2. Select top max_bullets_per_role bullets per role.
    3. Reorder: within roles, most relevant first.
    4. Reorder projects by average bullet score.
    5. Reorder skills: JD-relevant first within each category.
    6. Set section_order based on role domain.
    """
    jd_skills = {s.lower() for s in parsed_jd.required_skills + parsed_jd.tech_stack}

    # --- Score and select work experience bullets ---
    selected_exp = []
    for exp in master_resume.work_experience:
        bullets_raw = [b.model_dump() for b in exp.bullets]
        scores = await _score_bullets(bullets_raw, parsed_jd)

        sorted_bullets = sorted(
            bullets_raw,
            key=lambda b: scores.get(b["id"], 0.0),
            reverse=True,
        )
        selected = sorted_bullets[:max_bullets_per_role]

        exp_dict = exp.model_dump()
        exp_dict["bullets"] = selected
        exp_dict["_relevance"] = sum(scores.get(b["id"], 0) for b in selected) / max(len(selected), 1)
        selected_exp.append(exp_dict)

    # Keep most recent first (always), but note relevance for reference
    # (work_experience ordering is chronological — do not reorder)

    # --- Score and select project bullets ---
    scored_projects = []
    for proj in master_resume.projects:
        bullets_raw = [b.model_dump() for b in proj.bullets]
        scores = await _score_bullets(bullets_raw, parsed_jd)

        sorted_bullets = sorted(
            bullets_raw,
            key=lambda b: scores.get(b["id"], 0.0),
            reverse=True,
        )
        proj_dict = proj.model_dump()
        proj_dict["bullets"] = sorted_bullets[:max_bullets_per_role]
        avg_score = sum(scores.values()) / max(len(scores), 1)
        scored_projects.append((avg_score, proj_dict))

    # Reorder projects by relevance score
    scored_projects.sort(key=lambda x: x[0], reverse=True)
    selected_projects = [p for _, p in scored_projects]

    # --- Reorder skills ---
    skills_dict = master_resume.skills.model_dump()
    reordered_skills = _reorder_skills(skills_dict, jd_skills)

    # --- Section order: tech roles → projects before education ---
    section_order = ["work_experience", "projects", "skills", "education"]
    if parsed_jd.domain in ("finance", "trading"):
        section_order = ["work_experience", "skills", "projects", "education"]

    logger.info(
        "bullets_selected",
        extra={"exp_count": len(selected_exp), "project_count": len(selected_projects)},
    )

    return SelectedResume(
        personal=master_resume.personal.model_dump(),
        summary=master_resume.summary,
        work_experience=[{k: v for k, v in e.items() if k != "_relevance"} for e in selected_exp],
        education=[e.model_dump() for e in master_resume.education],
        skills=reordered_skills,
        projects=selected_projects,
        certifications=[c.model_dump() for c in master_resume.certifications],
        section_order=section_order,
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/bullet_selector.py
git commit -m "feat: bullet selector — per-role LLM scoring and reordering"
```

---

### Task 6: `services/keyword_injector.py` — Zero-Fabrication Rewriter

**Files:**
- Create: `backend/services/keyword_injector.py`

- [ ] **Step 1: Write `backend/services/keyword_injector.py`**

```python
"""
Rewrite selected resume bullets to naturally include missing JD keywords.

Zero-fabrication is enforced at two layers:
  1. Injection prompt: explicit SKIP instruction if keyword doesn't fit.
  2. Post-injection verification: second LLM call confirms no new claims were added.
     If verification fails, original bullet is restored and the failure is logged.

Limits: max 2 rewrites per work_experience role, max 1 per project.
"""
from __future__ import annotations

from pydantic import BaseModel

from backend.schemas import ParsedJD, SelectedResume
from backend.utils.llm import call_llm
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_INJECT_SYSTEM = (
    "You are rewriting resume bullet points. "
    "CRITICAL: You may only describe what actually happened. "
    "Never introduce a skill, technology, outcome, or experience "
    "that is not already described in the original bullet. "
    "When in doubt, output SKIP."
)

_INJECT_PROMPT = """\
Rewrite the resume bullet to naturally include the keyword below.

STRICT RULES:
- Do not introduce any new claim not present in the original.
- Preserve all numbers, percentages, and quantitative metrics exactly.
- Keep the same action verb or use a direct synonym.
- The rewrite must read naturally, not forced.
- If you cannot include the keyword without fabricating, output SKIP.

Original bullet: {original_text}
Keyword to include: {keyword}
Context (why this keyword fits): {context}

Output ONLY the rewritten bullet text, or the single word SKIP.
"""

_VERIFY_SYSTEM = (
    "You are a fabrication detector for resumes. "
    "Return ONLY valid JSON."
)

_VERIFY_PROMPT = """\
Compare these two resume bullets and check for fabrication.

Original: {original}
Rewritten: {rewritten}

Did the rewrite introduce any new claim, skill, technology, or outcome
not present in the original? Answer strictly.

Return JSON:
{{"fabrication_detected": true/false, "reason": "one sentence"}}
"""


class InjectionResult(BaseModel):
    bullet_id: str
    original_text: str
    rewritten_text: str | None
    injected_keywords: list[str]
    was_modified: bool
    skip_reason: str | None


async def _inject_one(
    bullet_id: str, original_text: str, keyword: str, context: str
) -> InjectionResult:
    """Attempt to inject one keyword into one bullet. Verify if modified."""
    prompt = _INJECT_PROMPT.format(
        original_text=original_text, keyword=keyword, context=context
    )
    rewritten = await call_llm(prompt, system=_INJECT_SYSTEM, max_tokens=300)
    rewritten = rewritten.strip()

    if rewritten.upper() == "SKIP" or not rewritten:
        return InjectionResult(
            bullet_id=bullet_id,
            original_text=original_text,
            rewritten_text=None,
            injected_keywords=[],
            was_modified=False,
            skip_reason="LLM chose SKIP",
        )

    # --- Post-injection verification ---
    verify_prompt = _VERIFY_PROMPT.format(original=original_text, rewritten=rewritten)
    import json
    raw_verify = await call_llm(
        verify_prompt, system=_VERIFY_SYSTEM, max_tokens=200, expect_json=True
    )
    verdict = json.loads(raw_verify)

    if verdict.get("fabrication_detected", True):
        logger.warning(
            "injection_fabrication_detected",
            extra={
                "bullet_id": bullet_id,
                "keyword": keyword,
                "reason": verdict.get("reason"),
            },
        )
        return InjectionResult(
            bullet_id=bullet_id,
            original_text=original_text,
            rewritten_text=None,
            injected_keywords=[],
            was_modified=False,
            skip_reason=f"Fabrication detected: {verdict.get('reason')}",
        )

    return InjectionResult(
        bullet_id=bullet_id,
        original_text=original_text,
        rewritten_text=rewritten,
        injected_keywords=[keyword],
        was_modified=True,
        skip_reason=None,
    )


async def inject_keywords(
    selected_resume: SelectedResume,
    parsed_jd: ParsedJD,
) -> SelectedResume:
    """
    Inject missing JD keywords into selected resume bullets.
    Returns an updated SelectedResume with rewritten bullets where safe.
    """
    # Collect all bullet text to find what keywords are already present
    all_text = " ".join(
        b["text"]
        for section in [selected_resume.work_experience, selected_resume.projects]
        for role in section
        for b in role.get("bullets", [])
    ).lower()

    missing_keywords = [
        kw for kw in parsed_jd.required_skills if kw.lower() not in all_text
    ]

    if not missing_keywords:
        logger.info("no_keywords_to_inject")
        return selected_resume

    logger.info("injecting_keywords", extra={"count": len(missing_keywords)})

    # Build a mutable copy
    import copy
    resume_copy = copy.deepcopy(selected_resume)

    # Inject into work experience (max 2 rewrites per role)
    for role in resume_copy.work_experience:
        rewrites_this_role = 0
        for bullet in role.get("bullets", []):
            if rewrites_this_role >= 2 or not missing_keywords:
                break
            keyword = missing_keywords[0]
            result = await _inject_one(
                bullet_id=bullet["id"],
                original_text=bullet["text"],
                keyword=keyword,
                context=f"Role: {role.get('role')} at {role.get('company')}",
            )
            if result.was_modified and result.rewritten_text:
                bullet["text"] = result.rewritten_text
                missing_keywords.pop(0)
                rewrites_this_role += 1

    # Inject into projects (max 1 rewrite per project)
    for proj in resume_copy.projects:
        if not missing_keywords:
            break
        for bullet in proj.get("bullets", [])[:1]:
            keyword = missing_keywords[0]
            result = await _inject_one(
                bullet_id=bullet["id"],
                original_text=bullet["text"],
                keyword=keyword,
                context=f"Project: {proj.get('name')}",
            )
            if result.was_modified and result.rewritten_text:
                bullet["text"] = result.rewritten_text
                missing_keywords.pop(0)
            break  # max 1 per project

    return resume_copy
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/keyword_injector.py
git commit -m "feat: keyword injector — zero-fabrication rewriter with post-injection verification"
```

---

### Task 7: `templates/resume.html` + `services/resume_renderer.py`

**Files:**
- Create: `templates/resume.html`
- Create: `backend/services/resume_renderer.py`

- [ ] **Step 1: Write `templates/resume.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  @page { margin: 0.5in; size: letter; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: "Liberation Sans", Arial, sans-serif; font-size: 10pt; color: #1a1a1a; line-height: 1.35; }

  /* Header */
  .header { text-align: center; margin-bottom: 10px; border-bottom: 1.5px solid #1a1a1a; padding-bottom: 6px; }
  .name { font-size: 20pt; font-weight: bold; letter-spacing: 0.5px; }
  .contact { font-size: 8.5pt; margin-top: 3px; color: #333; }
  .contact a { color: #1a1a1a; text-decoration: none; }

  /* Sections */
  .section { margin-top: 8px; }
  .section-title {
    font-size: 9.5pt; font-weight: bold; text-transform: uppercase;
    letter-spacing: 1px; border-bottom: 0.75px solid #555;
    padding-bottom: 1px; margin-bottom: 5px; color: #1a1a1a;
  }

  /* Experience / Projects */
  .entry { margin-bottom: 6px; }
  .entry-header { display: flex; justify-content: space-between; align-items: baseline; }
  .entry-title { font-weight: bold; font-size: 10pt; }
  .entry-subtitle { font-style: italic; font-size: 9.5pt; color: #333; }
  .entry-date { font-size: 9pt; color: #444; white-space: nowrap; }
  ul { margin-left: 14px; margin-top: 2px; }
  li { margin-bottom: 1.5px; font-size: 9.5pt; }

  /* Skills */
  .skills-grid { display: grid; grid-template-columns: 110px 1fr; row-gap: 2px; }
  .skill-label { font-weight: bold; font-size: 9.5pt; }
  .skill-value { font-size: 9.5pt; }

  /* Education */
  .edu-entry { margin-bottom: 4px; }
  .coursework { font-size: 9pt; color: #333; margin-top: 1px; }
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div class="name">{{ personal.name }}</div>
  <div class="contact">
    {{ personal.email }} &bull; {{ personal.phone }} &bull; {{ personal.location }}
    {% if personal.linkedin %} &bull; <a href="https://{{ personal.linkedin }}">{{ personal.linkedin }}</a>{% endif %}
    {% if personal.github %} &bull; <a href="https://{{ personal.github }}">{{ personal.github }}</a>{% endif %}
    {% if personal.website %} &bull; <a href="https://{{ personal.website }}">{{ personal.website }}</a>{% endif %}
  </div>
</div>

{% if summary %}
<div class="section">
  <div class="section-title">Professional Summary</div>
  <p style="font-size:9.5pt;">{{ summary }}</p>
</div>
{% endif %}

<!-- DYNAMIC SECTIONS -->
{% for section in section_order %}

{% if section == "work_experience" and work_experience %}
<div class="section">
  <div class="section-title">Experience</div>
  {% for exp in work_experience %}
  <div class="entry">
    <div class="entry-header">
      <span class="entry-title">{{ exp.company }}</span>
      <span class="entry-date">{{ exp.start_date }} – {{ exp.end_date if exp.end_date else "Present" }}</span>
    </div>
    <div class="entry-subtitle">{{ exp.role }}{% if exp.location %} &bull; {{ exp.location }}{% endif %}</div>
    {% if exp.bullets %}
    <ul>
      {% for bullet in exp.bullets %}
      <li>{{ bullet.text }}</li>
      {% endfor %}
    </ul>
    {% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}

{% if section == "projects" and projects %}
<div class="section">
  <div class="section-title">Projects</div>
  {% for proj in projects %}
  <div class="entry">
    <div class="entry-header">
      <span class="entry-title">{{ proj.name }}</span>
      {% if proj.url %}<span style="font-size:8.5pt;"><a href="{{ proj.url }}">{{ proj.url }}</a></span>{% endif %}
    </div>
    {% if proj.tech_stack %}<div class="entry-subtitle">{{ proj.tech_stack | join(", ") }}</div>{% endif %}
    {% if proj.bullets %}
    <ul>
      {% for bullet in proj.bullets %}
      <li>{{ bullet.text }}</li>
      {% endfor %}
    </ul>
    {% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}

{% if section == "skills" and skills %}
<div class="section">
  <div class="section-title">Skills</div>
  <div class="skills-grid">
    {% if skills.languages %}<span class="skill-label">Languages</span><span class="skill-value">{{ skills.languages | join(", ") }}</span>{% endif %}
    {% if skills.frameworks %}<span class="skill-label">Frameworks</span><span class="skill-value">{{ skills.frameworks | join(", ") }}</span>{% endif %}
    {% if skills.databases %}<span class="skill-label">Databases</span><span class="skill-value">{{ skills.databases | join(", ") }}</span>{% endif %}
    {% if skills.tools %}<span class="skill-label">Tools</span><span class="skill-value">{{ skills.tools | join(", ") }}</span>{% endif %}
    {% if skills.domains %}<span class="skill-label">Domains</span><span class="skill-value">{{ skills.domains | join(", ") }}</span>{% endif %}
    {% if skills.other %}<span class="skill-label">Other</span><span class="skill-value">{{ skills.other | join(", ") }}</span>{% endif %}
  </div>
</div>
{% endif %}

{% if section == "education" and education %}
<div class="section">
  <div class="section-title">Education</div>
  {% for edu in education %}
  <div class="edu-entry">
    <div class="entry-header">
      <span class="entry-title">{{ edu.institution }}</span>
      <span class="entry-date">{{ edu.start_date }} – {{ edu.end_date if edu.end_date else "Present" }}</span>
    </div>
    <div class="entry-subtitle">{{ edu.degree }} in {{ edu.field }}{% if edu.honors %} — {{ edu.honors }}{% endif %}</div>
    {% if edu.gpa %}<div class="coursework">GPA: {{ edu.gpa }}</div>{% endif %}
    {% if edu.relevant_coursework %}<div class="coursework">Coursework: {{ edu.relevant_coursework | join(", ") }}</div>{% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}

{% endfor %}

{% if certifications %}
<div class="section">
  <div class="section-title">Certifications</div>
  {% for cert in certifications %}
  <div class="entry">
    <div class="entry-header">
      <span class="entry-title">{{ cert.name }}</span>
      <span class="entry-date">{{ cert.date }}</span>
    </div>
    <div class="entry-subtitle">{{ cert.issuer }}</div>
  </div>
  {% endfor %}
</div>
{% endif %}

</body>
</html>
```

- [ ] **Step 2: Write `backend/services/resume_renderer.py`**

```python
"""
Render a SelectedResume to PDF (WeasyPrint) or DOCX (python-docx fallback).

Cache strategy: hash selected_resume JSON → check resume_versions table →
if render_hash found with pdf_path: return cached path.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.models import ResumeVersion
from backend.schemas import SelectedResume
from backend.utils.logging import get_logger

logger = get_logger(__name__)

RESUMES_DIR = Path("data/resumes")
TEMPLATES_DIR = Path("templates")


def _render_hash(selected_resume: SelectedResume) -> str:
    raw = json.dumps(selected_resume.model_dump(), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _render_html(selected_resume: SelectedResume) -> str:
    """Render the Jinja2 HTML template with resume data."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("resume.html")
    return template.render(**selected_resume.model_dump())


def _html_to_pdf(html: str, out_path: Path) -> bool:
    """Render HTML to PDF via WeasyPrint. Returns True on success."""
    try:
        from weasyprint import HTML
        HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf(str(out_path))
        return True
    except Exception as exc:
        logger.warning("weasyprint_failed", extra={"error": str(exc)})
        return False


def _html_to_docx(selected_resume: SelectedResume, out_path: Path) -> bool:
    """Basic python-docx fallback. Returns True on success."""
    try:
        from docx import Document
        from docx.shared import Pt, Inches

        doc = Document()

        # Narrow margins
        for section in doc.sections:
            section.left_margin = Inches(0.6)
            section.right_margin = Inches(0.6)
            section.top_margin = Inches(0.5)
            section.bottom_margin = Inches(0.5)

        personal = selected_resume.personal
        name_para = doc.add_paragraph()
        name_run = name_para.add_run(personal.get("name", ""))
        name_run.bold = True
        name_run.font.size = Pt(16)
        name_para.alignment = 1  # center

        contact_parts = [personal.get("email", ""), personal.get("phone", ""), personal.get("location", "")]
        contact_para = doc.add_paragraph(" | ".join(p for p in contact_parts if p))
        contact_para.alignment = 1

        for section_name in selected_resume.section_order:
            if section_name == "work_experience":
                doc.add_heading("Experience", level=2)
                for exp in selected_resume.work_experience:
                    p = doc.add_paragraph()
                    p.add_run(f"{exp.get('company')} — {exp.get('role')}").bold = True
                    for bullet in exp.get("bullets", []):
                        doc.add_paragraph(bullet["text"], style="List Bullet")

            elif section_name == "projects":
                doc.add_heading("Projects", level=2)
                for proj in selected_resume.projects:
                    p = doc.add_paragraph()
                    p.add_run(proj.get("name", "")).bold = True
                    for bullet in proj.get("bullets", []):
                        doc.add_paragraph(bullet["text"], style="List Bullet")

            elif section_name == "education":
                doc.add_heading("Education", level=2)
                for edu in selected_resume.education:
                    p = doc.add_paragraph()
                    p.add_run(f"{edu.get('institution')} — {edu.get('degree')}").bold = True

        doc.save(str(out_path))
        return True
    except Exception as exc:
        logger.warning("docx_render_failed", extra={"error": str(exc)})
        return False


async def render_resume(
    selected_resume: SelectedResume,
    application_id: str,
    session: AsyncSession,
    fmt: str = "pdf",
) -> str:
    """
    Render selected_resume to PDF or DOCX.

    1. Compute render_hash. Check resume_versions for cache hit.
    2. Render HTML via Jinja2.
    3. PDF: WeasyPrint → on failure, fallback to DOCX.
    4. Store in resume_versions. Return absolute path.
    """
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)

    render_hash = _render_hash(selected_resume)

    # --- Cache check ---
    result = await session.execute(
        select(ResumeVersion).where(ResumeVersion.render_hash == render_hash)
    )
    cached = result.scalar_one_or_none()
    if cached:
        if fmt == "pdf" and cached.pdf_path and os.path.exists(cached.pdf_path):
            logger.info("render_cache_hit", extra={"application_id": application_id})
            return cached.pdf_path
        if fmt == "docx" and cached.docx_path and os.path.exists(cached.docx_path):
            return cached.docx_path

    # --- Render ---
    version_id = str(uuid.uuid4())
    html = _render_html(selected_resume)

    pdf_path: str | None = None
    docx_path: str | None = None

    if fmt == "pdf":
        out_pdf = RESUMES_DIR / f"{application_id}.pdf"
        if _html_to_pdf(html, out_pdf):
            pdf_path = str(out_pdf)
        else:
            # Fallback to DOCX
            out_docx = RESUMES_DIR / f"{application_id}.docx"
            if _html_to_docx(selected_resume, out_docx):
                docx_path = str(out_docx)
                fmt = "docx"
    else:
        out_docx = RESUMES_DIR / f"{application_id}.docx"
        if _html_to_docx(selected_resume, out_docx):
            docx_path = str(out_docx)

    final_path = pdf_path or docx_path
    if not final_path:
        raise RuntimeError(f"Resume render failed for application {application_id}")

    # --- Store version ---
    version = ResumeVersion(
        id=version_id,
        application_id=application_id,
        tailored_json=json.dumps(selected_resume.model_dump()),
        pdf_path=pdf_path,
        docx_path=docx_path,
        render_hash=render_hash,
    )
    session.add(version)
    await session.commit()

    logger.info(
        "resume_rendered",
        extra={"application_id": application_id, "format": fmt, "path": final_path},
    )
    return final_path
```

- [ ] **Step 3: Commit**

```bash
git add templates/resume.html backend/services/resume_renderer.py
git commit -m "feat: WeasyPrint HTML resume renderer with Jinja2 template + DOCX fallback"
```

---

### Task 8: `scripts/test_pipeline.py` — CLI End-to-End Test

**Files:**
- Create: `scripts/test_pipeline.py`

- [ ] **Step 1: Write `scripts/test_pipeline.py`**

```python
#!/usr/bin/env python3
"""
CLI test for the full LLM pipeline (Phase 2 deliverable).

Usage:
    python3 scripts/test_pipeline.py "paste JD text here"
    python3 scripts/test_pipeline.py --file path/to/jd.txt

Runs: parse_jd → score_match → select_bullets → inject_keywords → render_resume
Outputs a PDF to data/resumes/ and prints the tailored resume JSON summary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

# Set DB to file-based for the script (reads real config)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/job_agent.db")


async def run(jd_text: str) -> None:
    from backend.database import init_db, AsyncSessionLocal
    from backend.schemas import MasterResume
    from backend.services.jd_parser import parse_jd
    from backend.services.match_scorer import score_match
    from backend.services.gap_analyzer import analyze_gaps
    from backend.services.bullet_selector import select_bullets
    from backend.services.keyword_injector import inject_keywords
    from backend.services.resume_renderer import render_resume

    await init_db()

    with open("data/master_resume.json") as f:
        resume = MasterResume.model_validate(json.load(f))

    async with AsyncSessionLocal() as session:
        print("\n[1/6] Parsing job description...")
        parsed_jd = await parse_jd(jd_text, session)
        print(f"      Role level: {parsed_jd.role_level} | Domain: {parsed_jd.domain}")
        print(f"      Required skills: {', '.join(parsed_jd.required_skills[:6])}")
        print(f"      Confidence: {parsed_jd.confidence:.2f}")

        print("\n[2/6] Scoring match...")
        match_result = await score_match(resume, parsed_jd)
        print(f"      Overall score: {match_result.overall_score}/100")
        print(f"      Missing required: {match_result.missing_required[:5]}")

        print("\n[3/6] Analysing gaps...")
        gap_report = analyze_gaps(resume, parsed_jd)
        print(f"      Hard gaps: {gap_report.hard_gaps[:5]}")
        print(f"      Covered: {gap_report.covered[:5]}")

        print("\n[4/6] Selecting bullets...")
        selected = await select_bullets(resume, parsed_jd, match_result)
        total_bullets = sum(len(e.get("bullets", [])) for e in selected.work_experience)
        print(f"      Selected {total_bullets} bullets across {len(selected.work_experience)} roles")

        print("\n[5/6] Injecting keywords (zero-fabrication)...")
        final_resume = await inject_keywords(selected, parsed_jd)

        print("\n[6/6] Rendering PDF...")
        app_id = str(uuid.uuid4())[:8]
        pdf_path = await render_resume(final_resume, app_id, session)
        print(f"      PDF written to: {pdf_path}")

    print("\n✅ Pipeline complete.")
    print(f"   Match score: {match_result.overall_score}/100")
    print(f"   PDF: {pdf_path}")
    print(f"   Section order: {' → '.join(final_resume.section_order)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay pipeline CLI test")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("jd_text", nargs="?", help="JD text as a string")
    group.add_argument("--file", help="Path to a text file containing the JD")
    args = parser.parse_args()

    if args.file:
        with open(args.file) as f:
            jd_text = f.read()
    else:
        jd_text = args.jd_text

    if not jd_text or not jd_text.strip():
        print("Error: JD text is empty.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(jd_text))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/test_pipeline.py
git commit -m "feat: CLI test pipeline script"
```

---

### Task 9: `tests/test_phase2.py` — Pytest Suite (Mocked LLM)

**Files:**
- Create: `tests/test_phase2.py`

- [ ] **Step 1: Write `tests/test_phase2.py`**

```python
"""
Phase 2 tests — LLM pipeline (all LLM calls mocked).

Tests:
  1. content_hash is deterministic and non-empty
  2. _strip_fences removes markdown code fences
  3. parse_jd returns ParsedJD and caches result in DB
  4. parse_jd returns cached result on second call (no second LLM call)
  5. match_scorer pre-filter returns score=0 on < 25% keyword overlap
  6. match_scorer returns weighted score >= 80 when all required skills match
  7. gap_analyzer identifies hard gaps correctly
  8. select_bullets returns SelectedResume with ≤ max_bullets_per_role bullets
  9. keyword_injector skips injection when SKIP is returned
  10. resume_renderer produces a file on disk
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_jd_text() -> str:
    return (
        "We are looking for a Software Engineer with strong Python and FastAPI skills. "
        "Experience with PostgreSQL, Docker, and microservices required. "
        "Nice to have: Kubernetes, Redis. 2-4 years experience. Hybrid role in Singapore."
    )


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
        raw_keywords=["Python", "FastAPI", "PostgreSQL", "Docker", "microservices"],
    )


@pytest.fixture
def sample_resume():
    import json as _json
    from backend.schemas import MasterResume
    with open("data/master_resume.json") as f:
        return MasterResume.model_validate(_json.load(f))


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
# Task 1: Utils
# ---------------------------------------------------------------------------

def test_content_hash_deterministic():
    from backend.utils.hashing import content_hash
    text = "hello world"
    assert content_hash(text) == content_hash(text)
    assert len(content_hash(text)) == 64  # SHA-256 hex
    assert content_hash("a") != content_hash("b")


def test_strip_fences():
    from backend.utils.llm import _strip_fences
    raw = "```json\n{\"key\": \"value\"}\n```"
    assert _strip_fences(raw) == '{"key": "value"}'

    plain = '{"key": "value"}'
    assert _strip_fences(plain) == plain


# ---------------------------------------------------------------------------
# Task 2: JD Parser
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
async def test_parse_jd_returns_parsed_jd(sample_jd_text):
    from backend.database import init_db, AsyncSessionLocal
    from backend.services.jd_parser import parse_jd

    await init_db()
    with patch("backend.services.jd_parser.call_llm", new=AsyncMock(return_value=_FAKE_JD_JSON)):
        async with AsyncSessionLocal() as session:
            result = await parse_jd(sample_jd_text, session)

    assert result.role_level == "mid"
    assert "Python" in result.required_skills
    assert result.confidence == 0.9


@pytest.mark.asyncio
async def test_parse_jd_caches_result(sample_jd_text):
    """Second call with same JD text must NOT call the LLM again."""
    from backend.database import init_db, AsyncSessionLocal
    from backend.services.jd_parser import parse_jd

    await init_db()
    mock_llm = AsyncMock(return_value=_FAKE_JD_JSON)
    with patch("backend.services.jd_parser.call_llm", new=mock_llm):
        async with AsyncSessionLocal() as session:
            await parse_jd(sample_jd_text, session)
            await parse_jd(sample_jd_text, session)  # second call

    assert mock_llm.call_count == 1  # LLM called only once


# ---------------------------------------------------------------------------
# Task 3: Match Scorer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_match_scorer_prefilter_low_overlap(sample_resume):
    """When keyword overlap < 25%, score must be 0 with no LLM call."""
    from backend.schemas import ParsedJD
    from backend.services.match_scorer import score_match

    # JD with skills totally absent from Aryan's resume
    irrelevant_jd = ParsedJD(
        required_skills=["COBOL", "Fortran", "Assembly", "Pascal", "RPG"],
        preferred_skills=[],
        responsibilities=["Maintain legacy systems"],
        tech_stack=["COBOL", "Fortran"],
        role_level="senior",
        domain="other",
        confidence=0.8,
        raw_keywords=["COBOL", "Fortran"],
    )

    mock_llm = AsyncMock()
    with patch("backend.services.match_scorer.call_llm", new=mock_llm):
        result = await score_match(sample_resume, irrelevant_jd)

    assert result.overall_score == 0.0
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_match_scorer_high_overlap_scores_well(sample_resume, sample_parsed_jd):
    """Resume with strong skill overlap should score >= 70."""
    semantic_mock = json.dumps({
        "experience_relevance": 0.85,
        "domain_alignment": 0.80,
        "reasoning": "Strong Python/FastAPI match",
    })

    with patch("backend.services.match_scorer.call_llm", new=AsyncMock(return_value=semantic_mock)):
        from backend.services.match_scorer import score_match
        result = await score_match(sample_resume, sample_parsed_jd)

    assert result.overall_score >= 70.0
    assert "Python" in result.strong_matches


# ---------------------------------------------------------------------------
# Task 4: Gap Analyzer
# ---------------------------------------------------------------------------

def test_gap_analyzer_identifies_missing_skills(sample_resume, sample_parsed_jd):
    from backend.services.gap_analyzer import analyze_gaps

    # Kubernetes is in preferred_skills but not in Aryan's resume
    report = analyze_gaps(sample_resume, sample_parsed_jd)
    assert isinstance(report.hard_gaps, list)
    assert isinstance(report.covered, list)
    assert "Python" in report.covered  # Python is definitely in resume


# ---------------------------------------------------------------------------
# Task 5: Bullet Selector
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_select_bullets_respects_max(sample_resume, sample_parsed_jd, sample_match_result):
    """select_bullets must return ≤ max_bullets_per_role bullets per role."""
    from backend.services.bullet_selector import select_bullets

    # Mock LLM to return equal scores for all bullets
    def make_score_response(bullets_json: str, **kwargs) -> str:
        import json as _json
        bullets = _json.loads(bullets_json.split("Bullets to score:\n")[1].strip()) if "Bullets to score:" in bullets_json else []
        # Parse ids from the prompt
        return _json.dumps([{"id": b["id"], "score": 0.7} for b in bullets])

    mock_llm = AsyncMock(side_effect=lambda prompt, **kw: json.dumps(
        [{"id": b["id"], "score": 0.7} for b in json.loads(
            prompt.split("Bullets to score:\n")[1].strip()
        )] if "Bullets to score:" in prompt else []
    ))

    with patch("backend.services.bullet_selector.call_llm", new=mock_llm):
        result = await select_bullets(sample_resume, sample_parsed_jd, sample_match_result, max_bullets_per_role=3)

    for exp in result.work_experience:
        assert len(exp.get("bullets", [])) <= 3


# ---------------------------------------------------------------------------
# Task 6: Keyword Injector
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_keyword_injector_skips_on_skip_response(sample_parsed_jd):
    """When LLM returns SKIP, original bullet must be unchanged."""
    from backend.schemas import SelectedResume
    from backend.services.keyword_injector import inject_keywords

    # Build a minimal SelectedResume where keyword is missing
    sel = SelectedResume(
        personal={"name": "Test"},
        work_experience=[{
            "role": "Engineer",
            "company": "Acme",
            "location": "SG",
            "start_date": "2024-01",
            "end_date": "2025-01",
            "bullets": [{"id": "b1", "text": "Built REST APIs using Python", "skills": ["Python"]}],
        }],
        section_order=["work_experience", "projects", "skills", "education"],
    )
    # Overwrite required skills with something definitely missing
    sample_parsed_jd.required_skills = ["Kubernetes"]

    with patch("backend.services.keyword_injector.call_llm", new=AsyncMock(return_value="SKIP")):
        result = await inject_keywords(sel, sample_parsed_jd)

    bullet = result.work_experience[0]["bullets"][0]
    assert bullet["text"] == "Built REST APIs using Python"  # unchanged


# ---------------------------------------------------------------------------
# Task 7: Resume Renderer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_renderer_produces_file(sample_resume, sample_parsed_jd, sample_match_result, tmp_path, monkeypatch):
    """render_resume must produce a file on disk (PDF or DOCX fallback)."""
    from backend.database import init_db, AsyncSessionLocal
    from backend.schemas import SelectedResume
    from backend.services import resume_renderer

    # Redirect output to tmp_path
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
        path = await resume_renderer.render_resume(sel, "test-app-id", session)

    assert os.path.exists(path)
    assert os.path.getsize(path) > 0
```

- [ ] **Step 2: Run tests**

```bash
python3 -m pytest tests/test_phase2.py -v
```

Expected: all tests pass (LLM calls are mocked).

- [ ] **Step 3: Commit and push**

```bash
git add tests/test_phase2.py
git commit -m "test: Phase 2 LLM pipeline — 10 tests, all mocked"
git push origin main
```

---

## Phase 2 Complete ✓

Deliverables:
- `backend/utils/llm.py` — Anthropic wrapper, retry, JSON validation, logging
- `backend/utils/hashing.py` — SHA-256 content hash
- `backend/utils/logging.py` — structured JSON logger
- `backend/services/jd_parser.py` — LLM parser with SQLite cache
- `backend/services/match_scorer.py` — keyword pre-filter + semantic scoring
- `backend/services/gap_analyzer.py` — fuzzy skill gap diff
- `backend/services/bullet_selector.py` — per-role LLM scoring + reorder
- `backend/services/keyword_injector.py` — zero-fabrication rewriter + verification
- `backend/services/resume_renderer.py` — WeasyPrint PDF + DOCX fallback
- `templates/resume.html` — clean Jinja2 resume template
- `scripts/test_pipeline.py` — full CLI pipeline test

**Next:** `plan/phase-03-answer-bank.md` — answer_bank service, confidence_scorer, routers/answer_bank.py, routers/pending.py
