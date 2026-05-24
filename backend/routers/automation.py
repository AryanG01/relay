"""
Automation API — called by local_agent.py running on the user's machine.

Routes:
  POST /api/automation/claim      claim the next TAILORING app for dispatch
  POST /api/automation/complete   mark a dispatch attempt complete
  GET  /api/automation/status     check if dispatch is enabled
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import Application, ApplicationStatus
from backend.services import app_queue
from backend.services.state_machine import transition
from backend.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/automation", tags=["automation"])


class DispatchResult(BaseModel):
    application_id: str
    status: str                     # applied | escalated | failed | assisted
    escalated_fields: list[str] = []
    error: str | None = None
    confirmation_url: str | None = None


@router.get("/status")
async def automation_status(session: AsyncSession = Depends(get_session)):
    """Return whether the dispatch system is active and how many apps are queued."""
    stats = await app_queue.get_queue_stats(session)
    return {
        "enabled": not stats.paused,
        "in_window": app_queue.is_dispatch_window(),
        "total_queued": stats.total_queued,
        "daily_dispatched": stats.daily_dispatched,
        "daily_cap": stats.daily_cap,
    }


@router.post("/claim")
async def claim_next(session: AsyncSession = Depends(get_session)):
    """
    Dequeue the next QUEUED application and move it to TAILORING.
    Returns the application dict for the local agent to process.
    """
    app = await app_queue.dequeue_next(session)
    if not app:
        return {"application": None, "reason": "queue_empty_or_capped"}

    logger.info("automation_claimed", extra={"app_id": app.id, "company": app.company})
    return {
        "application": {
            "id": app.id,
            "company": app.company,
            "role_title": app.role_title,
            "source_url": app.source_url,
            "source_platform": app.source_platform,
            "jd_raw": app.jd_raw,
            "status": app.status,
        }
    }


@router.post("/complete")
async def complete_dispatch(
    body: DispatchResult,
    session: AsyncSession = Depends(get_session),
):
    """
    Called by local_agent after it finishes attempting to fill a form.
    Updates the application status based on the result.
    """
    app = await session.get(Application, body.application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    status_map = {
        "applied":    ApplicationStatus.APPLIED,
        "failed":     ApplicationStatus.FAILED,
        "escalated":  ApplicationStatus.PENDING_CLARIFICATION,
    }

    new_status = status_map.get(body.status)
    if new_status and new_status != app.status:
        try:
            await transition(
                application_id=body.application_id,
                new_status=new_status,
                session=session,
                changed_by="local_agent",
                notes=body.error,
            )
        except Exception as exc:
            logger.warning("complete_transition_failed", extra={"error": str(exc)})

    logger.info("automation_complete", extra={
        "app_id": body.application_id,
        "result": body.status,
    })
    return {"ok": True}
