"""
Phase 4 tests — deduplicator, state machine, app queue, API routes.

Tests:
  1.  mark_seen + is_duplicate returns True for same URL
  2.  is_duplicate returns False for unseen URL
  3.  mark_seen is idempotent (no duplicate key error on repeat)
  4.  can_transition: valid pair returns True
  5.  can_transition: invalid pair returns False
  6.  transition: DISCOVERED → QUEUED writes StageHistory row
  7.  transition: invalid transition raises InvalidTransitionError
  8.  enqueue: sets status to QUEUED
  9.  dequeue_next: returns highest match_score app and sets TAILORING
  10. dequeue_next: returns None when queue empty
  11. get_queue_stats: counts reflect current DB state
  12. GET /api/applications returns all apps
  13. POST /api/applications creates app with DISCOVERED status
  14. POST /api/applications/{id}/retry re-queues FAILED app
  15. GET /api/applications/{id}/history returns history entries
"""
from __future__ import annotations

import os

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
    # Reset pause flag between tests
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


async def _make_app(session, *, status="discovered", match_score=75.0, company="Acme"):
    """Helper: insert a minimal Application row."""
    from backend.models import Application
    app = Application(
        company=company,
        role_title="Engineer",
        source_url=f"https://jobs.example.com/{company}",
        status=status,
        match_score=match_score,
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)
    return app


# ---------------------------------------------------------------------------
# 1–3: Deduplicator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_seen_then_is_duplicate(db_session):
    from backend.services.deduplicator import is_duplicate, mark_seen
    url = "https://jobs.example.com/swe-role"
    assert not await is_duplicate(url, db_session)
    await mark_seen(url, db_session, company="Acme", role_title="SWE")
    assert await is_duplicate(url, db_session)


@pytest.mark.asyncio
async def test_is_duplicate_false_for_new_url(db_session):
    from backend.services.deduplicator import is_duplicate
    assert not await is_duplicate("https://brand-new-url.example.com/job", db_session)


@pytest.mark.asyncio
async def test_mark_seen_idempotent(db_session):
    from backend.services.deduplicator import mark_seen
    url = "https://jobs.example.com/repeat"
    h1 = await mark_seen(url, db_session)
    h2 = await mark_seen(url, db_session)  # should not raise
    assert h1 == h2


# ---------------------------------------------------------------------------
# 4–7: State machine
# ---------------------------------------------------------------------------

def test_can_transition_valid():
    from backend.services.state_machine import can_transition
    assert can_transition("discovered", "queued") is True
    assert can_transition("failed", "queued") is True
    assert can_transition("applying", "applied") is True


def test_can_transition_invalid():
    from backend.services.state_machine import can_transition
    assert can_transition("applied", "discovered") is False
    assert can_transition("skipped", "queued") is False
    assert can_transition("expired", "applying") is False


@pytest.mark.asyncio
async def test_transition_writes_history(db_session):
    from sqlalchemy import select
    from backend.models import StageHistory
    from backend.services.state_machine import transition

    app = await _make_app(db_session, status="discovered")
    await transition(app.id, "queued", db_session, changed_by="test")

    result = await db_session.execute(
        select(StageHistory).where(StageHistory.application_id == app.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].from_status == "discovered"
    assert rows[0].to_status == "queued"


@pytest.mark.asyncio
async def test_transition_invalid_raises(db_session):
    from backend.services.state_machine import InvalidTransitionError, transition
    app = await _make_app(db_session, status="applied")
    with pytest.raises(InvalidTransitionError):
        await transition(app.id, "discovered", db_session)


# ---------------------------------------------------------------------------
# 8–11: App queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_sets_queued(db_session):
    from backend.services.app_queue import enqueue
    app = await _make_app(db_session, status="discovered")
    result = await enqueue(app.id, db_session)
    assert result.status == "queued"


@pytest.mark.asyncio
async def test_dequeue_returns_highest_score(db_session):
    from backend.services.app_queue import dequeue_next
    # Add two QUEUED apps with different scores
    low = await _make_app(db_session, status="queued", match_score=60.0, company="LowCo")
    high = await _make_app(db_session, status="queued", match_score=90.0, company="HighCo")

    dequeued = await dequeue_next(db_session)
    assert dequeued is not None
    assert dequeued.id == high.id
    assert dequeued.status == "tailoring"


@pytest.mark.asyncio
async def test_dequeue_returns_none_when_empty(db_session):
    from backend.services.app_queue import dequeue_next
    result = await dequeue_next(db_session)
    assert result is None


@pytest.mark.asyncio
async def test_get_queue_stats(db_session):
    from backend.services.app_queue import get_queue_stats
    await _make_app(db_session, status="queued", company="A")
    await _make_app(db_session, status="queued", company="B")
    await _make_app(db_session, status="discovered", company="C")

    stats = await get_queue_stats(db_session)
    assert stats.total_queued == 2
    assert stats.daily_cap == 15
    assert stats.paused is False


# ---------------------------------------------------------------------------
# 12–15: API routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_applications(api_client, db_session):
    await _make_app(db_session, company="ListCo")
    resp = await api_client.get("/api/applications")
    assert resp.status_code == 200
    assert any(a["company"] == "ListCo" for a in resp.json())


@pytest.mark.asyncio
async def test_create_application(api_client):
    resp = await api_client.post("/api/applications", json={
        "url": "https://jobs.example.com/new",
        "company": "NewCo",
        "role_title": "Backend Engineer",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["company"] == "NewCo"
    assert data["status"] == "discovered"


@pytest.mark.asyncio
async def test_retry_failed_application(api_client, db_session):
    app = await _make_app(db_session, status="failed")
    resp = await api_client.post(f"/api/applications/{app.id}/retry")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_get_history(api_client, db_session):
    from backend.services.state_machine import transition
    app = await _make_app(db_session, status="discovered")
    await transition(app.id, "queued", db_session)

    resp = await api_client.get(f"/api/applications/{app.id}/history")
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) >= 1
    assert history[0]["to_status"] == "queued"
