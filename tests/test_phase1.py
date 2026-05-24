"""
Phase 1 tests — data foundation.

Tests:
  1. master_resume.json loads and validates against MasterResume schema
  2. config.yaml loads into Settings without error
  3. init_db() creates all 7 tables in a fresh in-memory SQLite DB
  4. All ApplicationStatus and ApplicationStage enum values are non-empty strings
  5. ParsedJD schema accepts minimal fields and fills sensible defaults
  6. MatchResult schema stores all sub-scores correctly
"""
from __future__ import annotations

import json
import os

import pytest

# Use in-memory DB for all tests — never touch data/job_agent.db.
# Must be set before any backend imports so the engine is bound correctly.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"


def test_master_resume_loads_and_validates():
    """master_resume.json must parse cleanly against the MasterResume Pydantic model."""
    from backend.schemas import MasterResume

    resume_path = "data/master_resume.json"
    assert os.path.exists(resume_path), f"Missing {resume_path}"

    with open(resume_path) as f:
        raw = json.load(f)

    resume = MasterResume.model_validate(raw)

    assert resume.personal.name == "Aryan Ganju"
    assert resume.personal.email == "aryanganju01@gmail.com"
    assert len(resume.work_experience) >= 2
    assert len(resume.education) >= 1
    assert len(resume.skills.languages) > 0
    assert "Python" in resume.skills.languages

    # Every bullet must have an id and non-empty text
    for exp in resume.work_experience:
        for bullet in exp.bullets:
            assert bullet.id, f"Bullet missing id in {exp.company}"
            assert bullet.text.strip(), f"Empty bullet text in {exp.company}"
            assert 0.0 <= bullet.impact_score <= 1.0


def test_config_loads_from_yaml():
    """Settings must load from data/config.yaml with expected defaults."""
    from backend.config import get_settings, invalidate_settings_cache

    invalidate_settings_cache()
    settings = get_settings()

    assert settings.daily_cap == 15
    assert settings.min_match_score == 65.0
    assert settings.autofill_confidence_threshold == 0.85
    assert settings.work_auth_sg is True
    assert settings.work_auth_us is False
    assert settings.salary_sgd_min == 70000
    assert settings.salary_sgd_max == 95000
    assert settings.per_platform_caps["linkedin"] == 10


@pytest.mark.asyncio
async def test_init_db_creates_all_tables():
    """init_db() must create all 7 expected tables in a fresh DB."""
    from sqlalchemy import text

    from backend.database import engine, init_db

    await init_db()

    expected_tables = {
        "applications",
        "stage_history",
        "jd_cache",
        "resume_versions",
        "answer_bank",
        "pending_clarifications",
        "seen_hashes",
    }

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        actual_tables = {row[0] for row in result.fetchall()}

    assert expected_tables == actual_tables, (
        f"Table mismatch.\nExpected: {expected_tables}\nGot: {actual_tables}"
    )


def test_application_status_enum_values():
    """All ApplicationStatus values must be non-empty strings."""
    from backend.models import ApplicationStatus

    expected = {
        "discovered", "queued", "tailoring", "pending_clarification",
        "applying", "applied", "failed", "expired", "skipped",
    }
    actual = {s.value for s in ApplicationStatus}
    assert actual == expected


def test_application_stage_enum_values():
    """All ApplicationStage values must be non-empty strings."""
    from backend.models import ApplicationStage

    expected = {
        "none", "applied", "screening", "oa", "phone",
        "interview_1", "interview_2", "interview_3",
        "offer", "rejected", "ghosted", "withdrawn",
    }
    actual = {s.value for s in ApplicationStage}
    assert actual == expected


def test_parsed_jd_schema_defaults():
    """ParsedJD must accept minimal required fields and fill sensible defaults."""
    from backend.schemas import ParsedJD

    jd = ParsedJD(
        required_skills=["Python", "FastAPI"],
        preferred_skills=["Docker"],
        responsibilities=["Build REST APIs"],
        tech_stack=["Python", "FastAPI", "PostgreSQL"],
        role_level="mid",
        domain="software",
    )

    assert jd.remote_type == "unknown"
    assert jd.confidence == 1.0
    assert jd.sponsorship_available is None
    assert jd.years_experience_min is None


def test_match_result_schema():
    """MatchResult must store all sub-scores and categorised skills."""
    from backend.schemas import MatchResult

    result = MatchResult(
        overall_score=78.5,
        required_coverage=0.85,
        experience_relevance=0.80,
        domain_alignment=0.75,
        seniority_fit=0.90,
        missing_required=["Kubernetes"],
        strong_matches=["Python", "FastAPI"],
    )

    assert result.overall_score == 78.5
    assert "Kubernetes" in result.missing_required
    assert result.partial_required == []
