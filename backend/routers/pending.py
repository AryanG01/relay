"""
Human review queue for pending clarifications.

Routes:
  GET  /api/pending                     all pending clarifications
  POST /api/pending/{id}/resolve        resolve a field (optionally save to bank)
  POST /api/pending/{id}/skip           skip this field
  POST /api/pending/{id}/reject-app     reject the entire application
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import AnswerBank, Application, ApplicationStatus, PendingClarification
from backend.schemas import PendingClarificationResponse, ResolveField

router = APIRouter(prefix="/api/pending", tags=["pending"])


@router.get("", response_model=list[PendingClarificationResponse])
async def list_pending(session: AsyncSession = Depends(get_session)):
    """Return all pending clarifications, newest first."""
    result = await session.execute(
        select(PendingClarification)
        .where(PendingClarification.status == "pending")
        .order_by(PendingClarification.created_at.desc())
    )
    return result.scalars().all()


@router.post("/{clarification_id}/resolve", response_model=PendingClarificationResponse)
async def resolve_clarification(
    clarification_id: int,
    body: ResolveField,
    session: AsyncSession = Depends(get_session),
):
    """
    Resolve a pending field with a human-provided answer.
    Optionally saves the answer to the bank for future reuse.
    """
    row = await session.get(PendingClarification, clarification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Clarification not found")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already {row.status}")

    row.status = "resolved"
    row.resolved_answer = body.answer
    row.resolved_at = datetime.now(timezone.utc)

    if body.save_to_bank and row.field_name:
        existing = await session.execute(
            select(AnswerBank).where(AnswerBank.key == row.field_name)
        )
        bank_row = existing.scalar_one_or_none()
        if bank_row:
            bank_row.value = body.answer
        else:
            session.add(AnswerBank(key=row.field_name, value=body.answer))

    await session.commit()
    await session.refresh(row)
    return row


@router.post("/{clarification_id}/skip", response_model=PendingClarificationResponse)
async def skip_clarification(
    clarification_id: int,
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(PendingClarification, clarification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Clarification not found")
    row.status = "skipped"
    row.resolved_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/{clarification_id}/reject-app")
async def reject_application(
    clarification_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Reject the entire application linked to this clarification."""
    row = await session.get(PendingClarification, clarification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Clarification not found")

    row.status = "skipped"
    row.resolved_at = datetime.now(timezone.utc)

    app = await session.get(Application, row.application_id)
    if app:
        app.status = ApplicationStatus.SKIPPED

    await session.commit()
    return {"detail": "Application rejected", "application_id": row.application_id}
