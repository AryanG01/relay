"""
Phase 5 tests — browser automation (all Playwright calls mocked).

Tests:
  1.  BasePlatformHandler: safe_fill returns False on error (mocked page)
  2.  BasePlatformHandler: safe_select returns True on success
  3.  dispatch: escalates required fields to PendingClarification
  4.  dispatch: transitions app to PENDING_CLARIFICATION when fields escalate
  5.  dispatch: transitions app to APPLIED when mock handler reports submitted
  6.  dispatch: transitions app to FAILED when mock handler reports error
  7.  dispatch: logs error and returns failed status on unexpected exception
  8.  GET /api/automation/status returns paused=False by default
  9.  POST /api/automation/claim returns None when queue is empty
  10. POST /api/automation/claim returns app when one is QUEUED
  11. POST /api/automation/complete marks app APPLIED
  12. POST /api/automation/complete marks app FAILED
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def setup_db():
    from backend.database import Base, engine
    from backend.services import app_queue
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    app_queue._PAUSED = False


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


async def _make_app(session, *, status="discovered", match_score=75.0, platform="linkedin"):
    from backend.models import Application
    app = Application(
        company="TestCo",
        role_title="Engineer",
        source_url="https://linkedin.com/jobs/123",
        source_platform=platform,
        status=status,
        match_score=match_score,
        jd_raw="Python backend engineer role requiring Python, FastAPI",
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)
    return app


# ---------------------------------------------------------------------------
# 1–2: BasePlatformHandler helpers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safe_fill_returns_false_on_error():
    from backend.automation.base_handler import BasePlatformHandler, FormField, FillResult

    class _Dummy(BasePlatformHandler):
        platform = "test"
        async def detect_fields(self): return []
        async def fill_form(self, *a, **kw): return FillResult(success=True, submitted=True)
        async def detect_confirmation(self): return True

    mock_page = AsyncMock()
    mock_page.fill.side_effect = Exception("selector not found")
    handler = _Dummy(mock_page)
    result = await handler.safe_fill("#bad-selector", "value")
    assert result is False


@pytest.mark.asyncio
async def test_safe_select_returns_true_on_success():
    from backend.automation.base_handler import BasePlatformHandler, FillResult

    class _Dummy(BasePlatformHandler):
        platform = "test"
        async def detect_fields(self): return []
        async def fill_form(self, *a, **kw): return FillResult(success=True, submitted=True)
        async def detect_confirmation(self): return True

    mock_page = AsyncMock()
    mock_page.select_option = AsyncMock(return_value=None)
    handler = _Dummy(mock_page)
    result = await handler.safe_select("#select", "Option A")
    assert result is True


# ---------------------------------------------------------------------------
# 3–7: Dispatch orchestrator (Playwright mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_escalates_required_fields(db_session):
    """Required field with low confidence → PendingClarification created."""
    from backend.automation.dispatch import dispatch_application
    from backend.models import PendingClarification
    from sqlalchemy import select

    app = await _make_app(db_session, status="tailoring")

    # Mock decide_field to always escalate work_authorization (required)
    async def mock_decide(field_name, field_label, field_type, is_required, session, context=None):
        from backend.services.confidence_scorer import FieldDecision
        if is_required:
            return FieldDecision(
                field_name=field_name, field_label=field_label,
                decision="escalate", answer=None, confidence=0.0,
                reason="mocked escalation",
            )
        return FieldDecision(
            field_name=field_name, field_label=field_label,
            decision="autofill", answer="test_value", confidence=1.0,
            reason="mocked autofill",
        )

    with patch("backend.automation.dispatch.decide_field", new=mock_decide), \
         patch("backend.automation.dispatch.parse_jd", new=AsyncMock(return_value=MagicMock(raw_keywords=[]))):
        result = await dispatch_application(app.id, db_session)

    assert result["status"] == "escalated"
    assert len(result["escalated_fields"]) > 0

    pending = await db_session.execute(
        select(PendingClarification).where(PendingClarification.application_id == app.id)
    )
    assert len(pending.scalars().all()) > 0


@pytest.mark.asyncio
async def test_dispatch_transitions_to_pending_clarification(db_session):
    from backend.automation.dispatch import dispatch_application

    app = await _make_app(db_session, status="tailoring")

    async def mock_decide(field_name, field_label, field_type, is_required, session, context=None):
        from backend.services.confidence_scorer import FieldDecision
        return FieldDecision(
            field_name=field_name, field_label=field_label,
            decision="escalate" if is_required else "autofill",
            answer=None if is_required else "val",
            confidence=0.0 if is_required else 1.0,
            reason="mock",
        )

    with patch("backend.automation.dispatch.decide_field", new=mock_decide), \
         patch("backend.automation.dispatch.parse_jd", new=AsyncMock(return_value=MagicMock(raw_keywords=[]))):
        await dispatch_application(app.id, db_session)

    await db_session.refresh(app)
    assert app.status == "pending_clarification"


@pytest.mark.asyncio
async def test_dispatch_applied_when_handler_submits(db_session):
    """When all fields autofill and handler reports submitted → APPLIED."""
    from backend.automation.dispatch import dispatch_application
    from backend.automation.base_handler import FillResult

    app = await _make_app(db_session, status="tailoring")

    async def mock_decide(field_name, field_label, field_type, is_required, session, context=None):
        from backend.services.confidence_scorer import FieldDecision
        return FieldDecision(
            field_name=field_name, field_label=field_label,
            decision="autofill", answer="mock_val", confidence=1.0, reason="mock",
        )

    mock_fill_result = FillResult(success=True, submitted=True, confirmation_url="https://done.example.com")

    mock_handler = AsyncMock()
    mock_handler.fill_form = AsyncMock(return_value=mock_fill_result)
    mock_handler.take_screenshot = AsyncMock()

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.url = "https://done.example.com"

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=AsyncMock(new_page=AsyncMock(return_value=mock_page)))
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    handler_cls_mock = MagicMock(return_value=mock_handler)

    with patch("backend.automation.dispatch.decide_field", new=mock_decide), \
         patch("backend.automation.dispatch.parse_jd", new=AsyncMock(return_value=MagicMock(raw_keywords=[]))), \
         patch("backend.automation.dispatch.async_playwright", return_value=mock_pw), \
         patch("backend.automation.dispatch._get_handler_class", return_value=handler_cls_mock):
        result = await dispatch_application(app.id, db_session)

    assert result["status"] == "applied"
    await db_session.refresh(app)
    assert app.status == "applied"


@pytest.mark.asyncio
async def test_dispatch_failed_when_handler_errors(db_session):
    from backend.automation.dispatch import dispatch_application
    from backend.automation.base_handler import FillResult

    app = await _make_app(db_session, status="tailoring")

    async def mock_decide(field_name, field_label, field_type, is_required, session, context=None):
        from backend.services.confidence_scorer import FieldDecision
        return FieldDecision(
            field_name=field_name, field_label=field_label,
            decision="autofill", answer="val", confidence=1.0, reason="mock",
        )

    mock_fill_result = FillResult(success=False, submitted=False, error="Form submission crashed")

    mock_handler = AsyncMock()
    mock_handler.fill_form = AsyncMock(return_value=mock_fill_result)
    mock_handler.take_screenshot = AsyncMock()

    mock_page = AsyncMock()
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=AsyncMock(new_page=AsyncMock(return_value=mock_page)))
    mock_browser.close = AsyncMock()
    mock_pw = AsyncMock()
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    with patch("backend.automation.dispatch.decide_field", new=mock_decide), \
         patch("backend.automation.dispatch.parse_jd", new=AsyncMock(return_value=MagicMock(raw_keywords=[]))), \
         patch("backend.automation.dispatch.async_playwright", return_value=mock_pw), \
         patch("backend.automation.dispatch._get_handler_class", return_value=MagicMock(return_value=mock_handler)):
        result = await dispatch_application(app.id, db_session)

    assert result["status"] == "failed"
    await db_session.refresh(app)
    assert app.status == "failed"


@pytest.mark.asyncio
async def test_dispatch_returns_failed_on_exception(db_session):
    """Unexpected exception during Playwright → status=failed, no crash."""
    from backend.automation.dispatch import dispatch_application

    app = await _make_app(db_session, status="tailoring")

    async def mock_decide(*a, **kw):
        from backend.services.confidence_scorer import FieldDecision
        return FieldDecision(field_name="x", field_label="x", decision="autofill",
                             answer="v", confidence=1.0, reason="m")

    with patch("backend.automation.dispatch.decide_field", new=mock_decide), \
         patch("backend.automation.dispatch.parse_jd", new=AsyncMock(return_value=MagicMock(raw_keywords=[]))), \
         patch("backend.automation.dispatch.async_playwright", side_effect=RuntimeError("playwright crash")):
        result = await dispatch_application(app.id, db_session)

    assert result["status"] == "failed"
    assert "playwright crash" in (result["error"] or "")


# ---------------------------------------------------------------------------
# 8–12: Automation API routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_automation_status_not_paused(api_client):
    resp = await api_client.get("/api/automation/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert "total_queued" in data


@pytest.mark.asyncio
async def test_claim_returns_none_when_empty(api_client):
    resp = await api_client.post("/api/automation/claim")
    assert resp.status_code == 200
    assert resp.json()["application"] is None


@pytest.mark.asyncio
async def test_claim_returns_app_when_queued(api_client, db_session):
    app = await _make_app(db_session, status="queued")
    resp = await api_client.post("/api/automation/claim")
    assert resp.status_code == 200
    data = resp.json()
    assert data["application"] is not None
    assert data["application"]["id"] == app.id


@pytest.mark.asyncio
async def test_complete_marks_applied(api_client, db_session):
    app = await _make_app(db_session, status="applying")
    resp = await api_client.post("/api/automation/complete", json={
        "application_id": app.id,
        "status": "applied",
        "escalated_fields": [],
    })
    assert resp.status_code == 200
    await db_session.refresh(app)
    assert app.status == "applied"


@pytest.mark.asyncio
async def test_complete_marks_failed(api_client, db_session):
    app = await _make_app(db_session, status="applying")
    resp = await api_client.post("/api/automation/complete", json={
        "application_id": app.id,
        "status": "failed",
        "escalated_fields": [],
        "error": "Timeout",
    })
    assert resp.status_code == 200
    await db_session.refresh(app)
    assert app.status == "failed"
