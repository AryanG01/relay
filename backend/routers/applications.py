"""
Applications CRUD + stage management.

Routes:
  GET  /api/applications              list (filters: status, stage, platform)
  GET  /api/applications/{id}         get single
  POST /api/applications              create manually
  POST /api/applications/{id}/stage   update stage (for APPLIED apps)
  POST /api/applications/{id}/retry   re-queue a FAILED app
  GET  /api/applications/{id}/history stage history
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import Application, ApplicationStatus, StageHistory
from backend.schemas import ApplicationCreate, ApplicationResponse, ApplicationStageUpdate
from backend.services.state_machine import InvalidTransitionError, transition, update_stage
from backend.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("", response_model=list[ApplicationResponse])
async def list_applications(
    status: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    q = select(Application).order_by(Application.created_at.desc())
    if status:
        q = q.where(Application.status == status)
    if stage:
        q = q.where(Application.stage == stage)
    if platform:
        q = q.where(Application.source_platform == platform)
    result = await session.execute(q)
    return result.scalars().all()


@router.get("/{app_id}", response_model=ApplicationResponse)
async def get_application(app_id: str, session: AsyncSession = Depends(get_session)):
    app = await session.get(Application, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


@router.post("", response_model=ApplicationResponse, status_code=201)
async def create_application(
    body: ApplicationCreate,
    session: AsyncSession = Depends(get_session),
):
    app = Application(
        company=body.company,
        role_title=body.role_title,
        source_url=body.url,
        notes=body.notes,
        status=ApplicationStatus.DISCOVERED,
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)
    logger.info("application_created", extra={"app_id": app.id, "company": app.company})
    return app


@router.post("/{app_id}/stage", response_model=ApplicationResponse)
async def update_application_stage(
    app_id: str,
    body: ApplicationStageUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update the post-apply stage (Screening, OA, Interview, etc.)."""
    try:
        app = await update_stage(
            application_id=app_id,
            new_stage=body.stage,
            session=session,
            changed_by="human",
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return app


@router.post("/{app_id}/retry", response_model=ApplicationResponse)
async def retry_application(
    app_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Re-queue a FAILED application."""
    try:
        app = await transition(
            application_id=app_id,
            new_status=ApplicationStatus.QUEUED,
            session=session,
            changed_by="human",
            notes="Manual retry",
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return app


@router.get("/{app_id}/history")
async def get_history(app_id: str, session: AsyncSession = Depends(get_session)):
    """Return the full stage/status history for an application."""
    app = await session.get(Application, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    result = await session.execute(
        select(StageHistory)
        .where(StageHistory.application_id == app_id)
        .order_by(StageHistory.changed_at.asc())
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "from_status": r.from_status,
            "to_status": r.to_status,
            "from_stage": r.from_stage,
            "to_stage": r.to_stage,
            "changed_by": r.changed_by,
            "changed_at": r.changed_at,
            "notes": r.notes,
        }
        for r in rows
    ]
