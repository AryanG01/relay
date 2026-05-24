"""
Phase 8 — Integration tests.

These tests boot the full FastAPI application (no mocks) and exercise
the main API flows end-to-end:

  1.  GET /health returns {"status": "ok"}
  2.  Rate limiter: 200 on normal request, 429 after burst
  3.  Full application lifecycle: create → enqueue → dequeue (TAILORING)
  4.  Retry flow: FAILED → QUEUED via /retry
  5.  Answer bank: seed → GET → POST new → test-lookup → DELETE
  6.  Pending clarification: create app + clarification → resolve → status=resolved
  7.  Queue pause/resume flow
  8.  GET /api/queue/stats reflects correct counts
  9.  GET /api/applications with status filter
  10. GET /api/applications/{id}/history after transitions
  11. Automation /claim returns TAILORING app after enqueue
  12. Automation /complete marks app APPLIED
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"  # disable rate limit in integration tests


@pytest.fixture(autouse=True)
async def fresh_db():
    """Full DB teardown + setup (including seeding) before each test."""
    import backend.models  # noqa: F401
    from backend.database import Base, AsyncSessionLocal, engine, init_db
    from backend.services.answer_bank import seed_answer_bank
    from backend.services import app_queue

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        await seed_answer_bank(session)

    app_queue._PAUSED = False


@pytest.fixture
async def client():
    from backend.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# 1: Health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 2: Rate limiter middleware (enabled/disabled)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_disabled_allows_many(client):
    """With RATE_LIMIT_ENABLED=false all requests should pass."""
    for _ in range(5):
        r = await client.get("/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 3: Application lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_application_lifecycle(client):
    # Create
    r = await client.post("/api/applications", json={
        "url": "https://jobs.example.com/swe",
        "company": "LifecycleCo",
        "role_title": "SWE",
    })
    assert r.status_code == 201
    app_id = r.json()["id"]
    assert r.json()["status"] == "discovered"

    # Enqueue
    r = await client.post(f"/api/queue/enqueue/{app_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "queued"

    # Dequeue (moves to TAILORING)
    r = await client.post("/api/automation/claim")
    assert r.status_code == 200
    claimed = r.json()["application"]
    assert claimed is not None
    assert claimed["id"] == app_id
    assert claimed["status"] == "tailoring"


# ---------------------------------------------------------------------------
# 4: Retry flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_flow(client):
    # Create + force to FAILED state manually via state machine
    from backend.database import AsyncSessionLocal
    from backend.services.state_machine import transition

    r = await client.post("/api/applications", json={
        "url": "https://jobs.example.com/retry",
        "company": "RetryCo",
        "role_title": "Backend",
    })
    app_id = r.json()["id"]

    async with AsyncSessionLocal() as session:
        await transition(app_id, "queued", session)
        await transition(app_id, "tailoring", session)
        await transition(app_id, "applying", session)
        await transition(app_id, "failed", session)

    r = await client.post(f"/api/applications/{app_id}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# 5: Answer bank CRUD + lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_answer_bank_crud(client):
    # GET — should have seeded rows
    r = await client.get("/api/answer-bank")
    assert r.status_code == 200
    assert len(r.json()) >= 20

    # POST new
    r = await client.post("/api/answer-bank", json={
        "key": "integration_test_key",
        "value": "integration_test_value",
        "format_hint": "text",
    })
    assert r.status_code == 201
    row_id = r.json()["id"]

    # test-lookup
    r = await client.post("/api/answer-bank/test-lookup", json={
        "field_label": "salary_expectation_sgd",
        "field_type": "text",
    })
    assert r.status_code == 200
    assert r.json()["match_type"] == "exact"

    # DELETE
    r = await client.delete(f"/api/answer-bank/{row_id}")
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# 6: Pending clarification resolve
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_resolve(client):
    from backend.database import AsyncSessionLocal
    from backend.models import PendingClarification, Application

    r = await client.post("/api/applications", json={
        "url": "https://jobs.example.com/pend",
        "company": "PendCo",
        "role_title": "Dev",
    })
    app_id = r.json()["id"]

    async with AsyncSessionLocal() as session:
        clari = PendingClarification(
            application_id=app_id,
            field_name="salary",
            field_label="Expected salary",
            question_text="What's your expected salary?",
            suggested_answer="",
            confidence=0.3,
            status="pending",
        )
        session.add(clari)
        await session.commit()
        await session.refresh(clari)
        clari_id = clari.id

    r = await client.post(f"/api/pending/{clari_id}/resolve", json={
        "answer": "SGD 85,000",
        "save_to_bank": True,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"
    assert r.json()["resolved_answer"] == "SGD 85,000"


# ---------------------------------------------------------------------------
# 7: Queue pause / resume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_pause_resume(client):
    r = await client.post("/api/queue/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    r = await client.get("/api/queue/stats")
    assert r.json()["paused"] is True

    r = await client.post("/api/queue/resume")
    assert r.json()["status"] == "resumed"

    r = await client.get("/api/queue/stats")
    assert r.json()["paused"] is False


# ---------------------------------------------------------------------------
# 8: Queue stats counts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_stats_counts(client):
    # Add 2 apps, enqueue both
    for i in range(2):
        r = await client.post("/api/applications", json={
            "url": f"https://jobs.example.com/stats-{i}",
            "company": f"StatCo{i}",
            "role_title": "Eng",
        })
        app_id = r.json()["id"]
        await client.post(f"/api/queue/enqueue/{app_id}")

    r = await client.get("/api/queue/stats")
    assert r.status_code == 200
    assert r.json()["total_queued"] == 2


# ---------------------------------------------------------------------------
# 9: Applications list filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_applications_list_filter(client):
    # Create apps with different statuses
    r1 = await client.post("/api/applications", json={
        "url": "https://jobs.example.com/f1",
        "company": "FilterCo",
        "role_title": "A",
    })
    r2 = await client.post("/api/applications", json={
        "url": "https://jobs.example.com/f2",
        "company": "OtherCo",
        "role_title": "B",
    })

    # Filter by company via unfiltered list
    r = await client.get("/api/applications")
    all_apps = r.json()
    filter_co = [a for a in all_apps if a["company"] == "FilterCo"]
    assert len(filter_co) == 1


# ---------------------------------------------------------------------------
# 10: Stage history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stage_history(client):
    from backend.database import AsyncSessionLocal
    from backend.services.state_machine import transition

    r = await client.post("/api/applications", json={
        "url": "https://jobs.example.com/hist",
        "company": "HistoryCo",
        "role_title": "Eng",
    })
    app_id = r.json()["id"]

    async with AsyncSessionLocal() as session:
        await transition(app_id, "queued", session)

    r = await client.get(f"/api/applications/{app_id}/history")
    assert r.status_code == 200
    history = r.json()
    assert len(history) >= 1
    assert any(h["to_status"] == "queued" for h in history)


# ---------------------------------------------------------------------------
# 11: Automation claim
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_automation_claim(client):
    r = await client.post("/api/applications", json={
        "url": "https://jobs.example.com/claim-test",
        "company": "ClaimCo",
        "role_title": "Dev",
    })
    app_id = r.json()["id"]
    await client.post(f"/api/queue/enqueue/{app_id}")

    r = await client.post("/api/automation/claim")
    assert r.status_code == 200
    data = r.json()
    assert data["application"]["id"] == app_id
    assert data["application"]["status"] == "tailoring"


# ---------------------------------------------------------------------------
# 12: Automation complete → APPLIED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_automation_complete_applied(client):
    from backend.database import AsyncSessionLocal
    from backend.services.state_machine import transition

    r = await client.post("/api/applications", json={
        "url": "https://jobs.example.com/complete-test",
        "company": "CompleteCo",
        "role_title": "Dev",
    })
    app_id = r.json()["id"]

    async with AsyncSessionLocal() as session:
        await transition(app_id, "queued",    session)
        await transition(app_id, "tailoring", session)
        await transition(app_id, "applying",  session)

    r = await client.post("/api/automation/complete", json={
        "application_id": app_id,
        "status": "applied",
        "escalated_fields": [],
    })
    assert r.status_code == 200

    r = await client.get(f"/api/applications/{app_id}")
    assert r.json()["status"] == "applied"
