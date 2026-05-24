"""
Application state machine.

Enforces valid status transitions and writes an audit entry to
stage_history on every change.

Valid transitions (from → {valid targets}):
  DISCOVERED  → QUEUED, SKIPPED, EXPIRED
  QUEUED      → TAILORING, SKIPPED, EXPIRED
  TAILORING   → APPLYING, PENDING_CLARIFICATION, FAILED
  PENDING_CLARIFICATION → APPLYING, SKIPPED
  APPLYING    → APPLIED, FAILED
  FAILED      → QUEUED   (manual retry)
  APPLIED     → APPLIED  (stage updates only — status stays APPLIED)
  SKIPPED     → (terminal)
  EXPIRED     → (terminal)
"""
from __future__ import annotations

from backend.models import Application, ApplicationStage, ApplicationStatus, StageHistory
from backend.utils.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Map: current status → set of statuses it may transition to
VALID_TRANSITIONS: dict[str, set[str]] = {
    ApplicationStatus.DISCOVERED:           {ApplicationStatus.QUEUED, ApplicationStatus.SKIPPED, ApplicationStatus.EXPIRED},
    ApplicationStatus.QUEUED:               {ApplicationStatus.TAILORING, ApplicationStatus.SKIPPED, ApplicationStatus.EXPIRED},
    ApplicationStatus.TAILORING:            {ApplicationStatus.APPLYING, ApplicationStatus.PENDING_CLARIFICATION, ApplicationStatus.FAILED},
    ApplicationStatus.PENDING_CLARIFICATION:{ApplicationStatus.APPLYING, ApplicationStatus.SKIPPED},
    ApplicationStatus.APPLYING:             {ApplicationStatus.APPLIED, ApplicationStatus.FAILED},
    ApplicationStatus.FAILED:               {ApplicationStatus.QUEUED},
    ApplicationStatus.APPLIED:              {ApplicationStatus.APPLIED},  # stage-only updates
    ApplicationStatus.SKIPPED:              set(),
    ApplicationStatus.EXPIRED:              set(),
}


class InvalidTransitionError(ValueError):
    pass


def can_transition(from_status: str, to_status: str) -> bool:
    """Return True if transitioning from_status → to_status is allowed."""
    return to_status in VALID_TRANSITIONS.get(from_status, set())


async def transition(
    application_id: str,
    new_status: str,
    session: AsyncSession,
    new_stage: str | None = None,
    changed_by: str = "system",
    notes: str | None = None,
) -> Application:
    """
    Transition an application to a new status (and optionally a new stage).

    Raises InvalidTransitionError if the transition is not permitted.
    Writes a StageHistory row regardless.
    """
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"Application {application_id!r} not found")

    from_status = app.status
    from_stage = app.stage

    if not can_transition(from_status, new_status):
        raise InvalidTransitionError(
            f"Cannot transition {from_status!r} → {new_status!r} "
            f"for application {application_id}"
        )

    resolved_stage = new_stage or from_stage

    # Write history first
    session.add(StageHistory(
        application_id=application_id,
        from_status=from_status,
        to_status=new_status,
        from_stage=from_stage,
        to_stage=resolved_stage,
        changed_by=changed_by,
        notes=notes,
    ))

    app.status = new_status
    app.stage = resolved_stage

    await session.commit()
    await session.refresh(app)

    logger.info(
        "app_transition",
        extra={
            "app_id": application_id,
            "from": from_status,
            "to": new_status,
            "stage": resolved_stage,
            "by": changed_by,
        },
    )
    return app


async def update_stage(
    application_id: str,
    new_stage: str,
    session: AsyncSession,
    changed_by: str = "human",
    notes: str | None = None,
) -> Application:
    """
    Update only the stage on an APPLIED application.
    Status stays APPLIED; a history row is written.
    """
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"Application {application_id!r} not found")

    from_stage = app.stage

    session.add(StageHistory(
        application_id=application_id,
        from_status=app.status,
        to_status=app.status,
        from_stage=from_stage,
        to_stage=new_stage,
        changed_by=changed_by,
        notes=notes,
    ))

    app.stage = new_stage
    await session.commit()
    await session.refresh(app)

    logger.info(
        "stage_updated",
        extra={"app_id": application_id, "from_stage": from_stage, "to_stage": new_stage},
    )
    return app
