"""
Queue management routes.

Routes:
  GET  /api/queue/stats           queue statistics
  POST /api/queue/pause           pause dispatch
  POST /api/queue/resume          resume dispatch
  POST /api/queue/enqueue/{id}    manually enqueue a DISCOVERED app
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import Application, ApplicationStatus
from backend.schemas import QueueStats
from backend.services import app_queue
from backend.services.state_machine import InvalidTransitionError

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("/stats", response_model=QueueStats)
async def queue_stats(session: AsyncSession = Depends(get_session)):
    return await app_queue.get_queue_stats(session)


@router.post("/pause")
async def pause():
    app_queue.pause_queue()
    return {"status": "paused"}


@router.post("/resume")
async def resume():
    app_queue.resume_queue()
    return {"status": "resumed"}


@router.post("/enqueue/{app_id}", response_model=dict)
async def manual_enqueue(
    app_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Manually move a DISCOVERED application to the QUEUED state."""
    application = await session.get(Application, app_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status != ApplicationStatus.DISCOVERED:
        raise HTTPException(
            status_code=409,
            detail=f"Application is {application.status!r}, not DISCOVERED",
        )
    try:
        app = await app_queue.enqueue(app_id, session)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"application_id": app.id, "status": app.status}
