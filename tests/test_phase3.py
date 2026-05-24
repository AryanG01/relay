"""
Phase 3 tests — answer bank + confidence scorer + API routes.

Tests:
  1.  seed_answer_bank inserts expected keys
  2.  lookup_answer returns exact match with confidence 1.0
  3.  lookup_answer returns fuzzy match for near-synonym label
  4.  lookup_answer returns not_found for unknown label
  5.  should_always_escalate catches cover letter pattern
  6.  should_always_escalate catches motivational patterns
  7.  should_always_escalate does NOT flag salary/yoe/location questions
  8.  decide_field returns autofill for high-confidence match
  9.  decide_field returns escalate for always-escalate pattern
  10. decide_field returns escalate for required field below threshold
  11. decide_field returns skip for optional field below threshold
  12. GET /api/answer-bank returns seeded rows
  13. POST /api/answer-bank adds a new answer
  14. POST /api/answer-bank/test-lookup returns correct result
  15. POST /api/pending/{id}/resolve marks as resolved
  16. POST /api/pending/{id}/reject-app sets application status to skipped
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# DB setup fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def setup_db():
    """Drop/recreate all tables and seed answer bank before each test."""
    from backend.database import AsyncSessionLocal, Base, engine, init_db
    from backend.services.answer_bank import seed_answer_bank

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        await seed_answer_bank(session)


@pytest.fixture
async def db_session():
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def api_client():
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# 1–4: Answer bank lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_inserts_expected_keys(db_session):
    from sqlalchemy import select
    from backend.models import AnswerBank
    result = await db_session.execute(select(AnswerBank))
    keys = {row.key for row in result.scalars().all()}
    assert "salary_expectation_sgd" in keys
    assert "phone_number" in keys
    assert "work_authorization_sg" in keys
    assert len(keys) >= 20


@pytest.mark.asyncio
async def test_lookup_exact_match(db_session):
    from backend.services.answer_bank import lookup_answer
    result = await lookup_answer("salary_expectation_sgd", "text", db_session)
    assert result.confidence == 1.0
    assert result.match_type == "exact"
    assert "SGD" in result.value


@pytest.mark.asyncio
async def test_lookup_fuzzy_match(db_session):
    from backend.services.answer_bank import lookup_answer
    # "notice period" should fuzzy-match the stored key "notice_period_weeks" (~81%)
    result = await lookup_answer("notice period", "text", db_session)
    assert result.match_type in ("exact", "fuzzy")
    assert result.confidence > 0.5


@pytest.mark.asyncio
async def test_lookup_not_found(db_session):
    from backend.services.answer_bank import lookup_answer
    result = await lookup_answer("xyzzy_unknown_field_abc", "text", db_session)
    assert result.match_type == "not_found"
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# 5–7: Always-escalate patterns
# ---------------------------------------------------------------------------

def test_always_escalate_cover_letter():
    from backend.services.answer_bank import should_always_escalate
    assert should_always_escalate("Cover Letter") is True
    assert should_always_escalate("Upload your cover letter") is True


def test_always_escalate_motivation():
    from backend.services.answer_bank import should_always_escalate
    assert should_always_escalate("Why do you want to join us?") is True
    assert should_always_escalate("Tell us about yourself") is True
    assert should_always_escalate("What motivates you?") is True


def test_always_escalate_does_not_flag_salary():
    from backend.services.answer_bank import should_always_escalate
    assert should_always_escalate("Expected salary (SGD)") is False
    assert should_always_escalate("Years of experience") is False
    assert should_always_escalate("Current location") is False


# ---------------------------------------------------------------------------
# 8–11: Confidence scorer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decide_field_autofill(db_session):
    from backend.services.confidence_scorer import decide_field
    decision = await decide_field(
        field_name="phone",
        field_label="phone_number",
        field_type="text",
        is_required=True,
        session=db_session,
    )
    assert decision.decision == "autofill"
    assert decision.confidence >= 0.85
    assert decision.answer is not None


@pytest.mark.asyncio
async def test_decide_field_escalate_pattern(db_session):
    from backend.services.confidence_scorer import decide_field
    decision = await decide_field(
        field_name="cover_letter",
        field_label="Please write a cover letter",
        field_type="text",
        is_required=True,
        session=db_session,
    )
    assert decision.decision == "escalate"
    assert "always-escalate" in decision.reason.lower()


@pytest.mark.asyncio
async def test_decide_field_escalate_required_low_confidence(db_session):
    from backend.services.confidence_scorer import decide_field
    decision = await decide_field(
        field_name="unknown_required_field",
        field_label="xyzzy_completely_unknown_field_required",
        field_type="text",
        is_required=True,
        session=db_session,
    )
    assert decision.decision == "escalate"


@pytest.mark.asyncio
async def test_decide_field_skip_optional_low_confidence(db_session):
    from backend.services.confidence_scorer import decide_field
    decision = await decide_field(
        field_name="unknown_optional_field",
        field_label="xyzzy_completely_unknown_field_optional",
        field_type="text",
        is_required=False,
        session=db_session,
    )
    assert decision.decision == "skip"


# ---------------------------------------------------------------------------
# 12–14: Answer bank API routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_answer_bank_returns_seeded_rows(api_client):
    resp = await api_client.get("/api/answer-bank")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 20
    keys = {row["key"] for row in data}
    assert "salary_expectation_sgd" in keys


@pytest.mark.asyncio
async def test_post_answer_bank_adds_row(api_client):
    resp = await api_client.post("/api/answer-bank", json={
        "key": "test_custom_key",
        "value": "custom_value",
        "format_hint": "text",
    })
    assert resp.status_code == 201
    assert resp.json()["key"] == "test_custom_key"


@pytest.mark.asyncio
async def test_test_lookup_endpoint(api_client):
    resp = await api_client.post("/api/answer-bank/test-lookup", json={
        "field_label": "salary_expectation_sgd",
        "field_type": "text",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["match_type"] == "exact"
    assert data["confidence"] == 1.0


# ---------------------------------------------------------------------------
# 15–16: Pending clarifications
# ---------------------------------------------------------------------------

@pytest.fixture
async def pending_row(db_session):
    """Insert a test application + pending clarification, return clarification id."""
    from backend.models import Application, PendingClarification
    app = Application(
        id="test-app-001",
        company="TestCo",
        role_title="Engineer",
    )
    db_session.add(app)
    clari = PendingClarification(
        application_id="test-app-001",
        field_name="custom_field",
        field_label="Custom Field",
        question_text="What is your custom answer?",
        suggested_answer="Maybe",
        confidence=0.4,
        status="pending",
    )
    db_session.add(clari)
    await db_session.commit()
    await db_session.refresh(clari)
    return clari.id


@pytest.mark.asyncio
async def test_resolve_clarification(api_client, pending_row):
    resp = await api_client.post(
        f"/api/pending/{pending_row}/resolve",
        json={"answer": "My final answer", "save_to_bank": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"
    assert data["resolved_answer"] == "My final answer"


@pytest.mark.asyncio
async def test_reject_app_sets_skipped(api_client, pending_row, db_session):
    resp = await api_client.post(f"/api/pending/{pending_row}/reject-app")
    assert resp.status_code == 200
    assert resp.json()["application_id"] == "test-app-001"

    from backend.models import Application
    app = await db_session.get(Application, "test-app-001")
    assert app.status == "skipped"
