"""
Phase 6 tests — scrapers, red-flag detector, discovery pipeline.

Tests:
  1.  red_flag: staffing company name triggers red flag
  2.  red_flag: visa-denied keyword in JD triggers red flag
  3.  red_flag: contract title triggers red flag (when exclude_contract_only=True)
  4.  red_flag: salary below minimum triggers red flag
  5.  red_flag: normal listing returns is_red_flag=False
  6.  red_flag: non-tech title (sales) is flagged
  7.  discovery: duplicate URL returns "duplicate" outcome
  8.  discovery: red-flag listing returns "red_flag" outcome
  9.  discovery: good listing gets queued when score >= threshold (LLM mocked)
  10. discovery: low-score listing gets skipped (LLM mocked)
  11. expire_stale: old QUEUED apps transition to EXPIRED
  12. expire_stale: recent apps are NOT expired
  13. IndeedScraper._parse_jobs_from_html parses job data
  14. FakeScraper returns injected listings
  15. discovery run_discovery counts match outcomes
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def setup_db():
    import backend.models  # noqa: F401 — ensure all tables registered
    from backend.database import Base, engine
    from backend.config import invalidate_settings_cache
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    invalidate_settings_cache()


@pytest.fixture
async def db_session():
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
def resume_data():
    with open("data/master_resume.json") as f:
        return json.load(f)


def _make_listing(**kw):
    from backend.scrapers.base_scraper import JobListing
    defaults = dict(
        url="https://jobs.example.com/swe-001",
        title="Software Engineer",
        company="Acme Corp",
        location="Singapore",
        platform="linkedin",
        raw_jd="Python FastAPI backend engineer role. Required: Python, FastAPI, PostgreSQL.",
        salary_text="",
        is_easy_apply=True,
    )
    defaults.update(kw)
    return JobListing(**defaults)


# ---------------------------------------------------------------------------
# 1–6: Red flag detector
# ---------------------------------------------------------------------------

def test_red_flag_staffing_company():
    from backend.scrapers.red_flag_detector import detect_red_flags
    listing = _make_listing(company="TechStaffing Solutions")
    result = detect_red_flags(listing)
    assert result.is_red_flag is True
    assert any("staffing" in r.lower() for r in result.reasons)


def test_red_flag_visa_keyword():
    from backend.scrapers.red_flag_detector import detect_red_flags
    listing = _make_listing(raw_jd="Must be US citizen only. No sponsorship available.")
    result = detect_red_flags(listing)
    assert result.is_red_flag is True


def test_red_flag_contract_title():
    from backend.scrapers.red_flag_detector import detect_red_flags
    from backend.config import get_settings, invalidate_settings_cache
    # Patch settings to exclude contracts
    with patch.object(get_settings(), "exclude_contract_only", True):
        listing = _make_listing(title="Contract Software Engineer")
        result = detect_red_flags(listing)
    assert result.is_red_flag is True


def test_red_flag_salary_below_minimum():
    from backend.scrapers.red_flag_detector import detect_red_flags
    listing = _make_listing(salary_text="SGD 30,000 per year")
    result = detect_red_flags(listing)
    assert result.is_red_flag is True
    assert any("30000" in r or "Salary" in r for r in result.reasons)


def test_red_flag_normal_listing():
    from backend.scrapers.red_flag_detector import detect_red_flags
    listing = _make_listing(
        company="FinTech Startup",
        title="Backend Engineer",
        raw_jd="Python FastAPI role in Singapore. Great team, hybrid work.",
        salary_text="SGD 90,000 per year",
    )
    result = detect_red_flags(listing)
    assert result.is_red_flag is False


def test_red_flag_non_tech_title():
    from backend.scrapers.red_flag_detector import detect_red_flags
    listing = _make_listing(title="Sales Manager")
    result = detect_red_flags(listing)
    assert result.is_red_flag is True
    assert any("sales" in r.lower() for r in result.reasons)


# ---------------------------------------------------------------------------
# 7–10: Discovery pipeline (LLM mocked)
# ---------------------------------------------------------------------------

_FAKE_JD_JSON = json.dumps({
    "required_skills": ["Python", "FastAPI"],
    "preferred_skills": [],
    "responsibilities": ["Build APIs"],
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

_FAKE_SEMANTIC = json.dumps({
    "experience_relevance": 0.85,
    "domain_alignment": 0.80,
    "reasoning": "Good match",
})


@pytest.mark.asyncio
async def test_discovery_duplicate(db_session, resume_data):
    from backend.scrapers.discovery import process_listing
    from backend.services.deduplicator import mark_seen

    listing = _make_listing()
    await mark_seen(listing.url, db_session, listing.company, listing.title)
    outcome = await process_listing(listing, resume_data, db_session)
    assert outcome == "duplicate"


@pytest.mark.asyncio
async def test_discovery_red_flag(db_session, resume_data):
    from backend.scrapers.discovery import process_listing

    listing = _make_listing(company="BestStaffing Inc", raw_jd="")
    outcome = await process_listing(listing, resume_data, db_session)
    assert outcome == "red_flag"


@pytest.mark.asyncio
async def test_discovery_queued_on_good_match(db_session, resume_data):
    from backend.scrapers.discovery import process_listing

    listing = _make_listing(url="https://jobs.example.com/unique-good")
    with patch("backend.scrapers.discovery.parse_jd", new=AsyncMock(
            return_value=_parse_fake_jd())), \
         patch("backend.scrapers.discovery.score_match", new=AsyncMock(
            return_value=_fake_match(80.0))):
        outcome = await process_listing(listing, resume_data, db_session)

    assert outcome == "queued"


@pytest.mark.asyncio
async def test_discovery_skipped_low_score(db_session, resume_data):
    from backend.scrapers.discovery import process_listing

    listing = _make_listing(url="https://jobs.example.com/unique-low")
    with patch("backend.scrapers.discovery.parse_jd", new=AsyncMock(
            return_value=_parse_fake_jd())), \
         patch("backend.scrapers.discovery.score_match", new=AsyncMock(
            return_value=_fake_match(30.0))):
        outcome = await process_listing(listing, resume_data, db_session)

    assert outcome == "skipped"


def _parse_fake_jd():
    from backend.schemas import ParsedJD
    return ParsedJD(
        required_skills=["Python", "FastAPI"],
        preferred_skills=[],
        responsibilities=["Build APIs"],
        tech_stack=["Python"],
        role_level="mid",
        domain="software",
        confidence=0.9,
        raw_keywords=["Python"],
    )


def _fake_match(score: float):
    from backend.schemas import MatchResult
    return MatchResult(
        overall_score=score,
        required_coverage=0.8,
        experience_relevance=0.8,
        domain_alignment=0.8,
        seniority_fit=0.8,
        strong_matches=["Python"],
    )


# ---------------------------------------------------------------------------
# 11–12: Expiry sweep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expire_stale_old_app(db_session):
    from backend.models import Application, ApplicationStatus
    from backend.scrapers.discovery import expire_stale_applications

    # Insert an app created 20 days ago
    old_app = Application(
        company="OldCo",
        role_title="SWE",
        status=ApplicationStatus.QUEUED,
    )
    db_session.add(old_app)
    await db_session.commit()

    # Manually backdate created_at
    old_time = datetime.now(timezone.utc) - timedelta(days=20)
    from sqlalchemy import update
    from backend.models import Application as AppModel
    await db_session.execute(
        update(AppModel).where(AppModel.id == old_app.id).values(created_at=old_time)
    )
    await db_session.commit()

    count = await expire_stale_applications(db_session)
    assert count >= 1
    await db_session.refresh(old_app)
    assert old_app.status == "expired"


@pytest.mark.asyncio
async def test_expire_stale_recent_app_not_expired(db_session):
    from backend.models import Application, ApplicationStatus
    from backend.scrapers.discovery import expire_stale_applications

    recent = Application(company="NewCo", role_title="SWE", status=ApplicationStatus.QUEUED)
    db_session.add(recent)
    await db_session.commit()

    count = await expire_stale_applications(db_session)
    assert count == 0
    await db_session.refresh(recent)
    assert recent.status == "queued"


# ---------------------------------------------------------------------------
# 13–15: Scraper utilities + end-to-end run
# ---------------------------------------------------------------------------

def test_indeed_parse_html():
    from backend.scrapers.indeed_scraper import _parse_jobs_from_html
    # Simulate Indeed JSON embed pattern
    html = '''
    "jobKey":"abc123","title":"Software Engineer","company":"Acme Corp"
    "jobKey":"def456","title":"Backend Developer","company":"StartupXYZ"
    '''
    results = _parse_jobs_from_html(html, "https://sg.indeed.com")
    assert len(results) == 2
    assert results[0].url == "https://sg.indeed.com/viewjob?jk=abc123"
    assert results[0].title == "Software Engineer"


@pytest.mark.asyncio
async def test_fake_scraper_returns_listings():
    """FakeScraper is our test double — verify it behaves like BaseScraper."""
    from backend.scrapers.base_scraper import BaseScraper, JobListing

    class FakeScraper(BaseScraper):
        platform = "fake"
        def __init__(self, listings): self._listings = listings
        async def scrape(self, query, location="Singapore", max_results=50):
            return self._listings[:max_results]

    listings = [_make_listing(url=f"https://fake.example.com/{i}") for i in range(3)]
    scraper = FakeScraper(listings)
    result = await scraper.scrape("engineer", max_results=2)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_run_discovery_counts(db_session, resume_data):
    """End-to-end: FakeScraper → discovery pipeline → counts add up."""
    from backend.scrapers.base_scraper import BaseScraper
    from backend.scrapers.discovery import run_discovery

    class FakeScraper(BaseScraper):
        platform = "fake"
        async def scrape(self, query, location="Singapore", max_results=50):
            return [
                _make_listing(url="https://fake.example.com/good", company="GoodCo"),
                _make_listing(url="https://fake.example.com/rf",   company="TopStaffing"),
            ]

    with patch("backend.scrapers.discovery.parse_jd", new=AsyncMock(
            return_value=_parse_fake_jd())), \
         patch("backend.scrapers.discovery.score_match", new=AsyncMock(
            return_value=_fake_match(80.0))):
        counts = await run_discovery(
            db_session, [FakeScraper()], resume_data
        )

    assert counts["scraped"] == 2
    assert counts["queued"] + counts["red_flag"] + counts["skipped"] + counts["error"] == 2
    assert counts["red_flag"] >= 1   # TopStaffing triggers staffing filter
