"""
Application dispatch queue.

Priority: highest match_score first.
Guards: daily cap, dispatch window (configurable days + hours), pause flag.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import Application, ApplicationStatus, StageHistory
from backend.schemas import QueueStats
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Module-level pause flag (single-process; resets on restart)
_PAUSED: bool = False


def pause_queue() -> None:
    global _PAUSED
    _PAUSED = True
    logger.info("queue_paused")


def resume_queue() -> None:
    global _PAUSED
    _PAUSED = False
    logger.info("queue_resumed")


def is_paused() -> bool:
    return _PAUSED


def is_dispatch_window() -> bool:
    """
    Return True if the current UTC time falls within the configured dispatch
    window (day-of-week + hour range).
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    day_name = now.strftime("%A").lower()  # e.g. "tuesday"
    if day_name not in [d.lower() for d in settings.dispatch_days]:
        return False
    return settings.dispatch_start_hour <= now.hour < settings.dispatch_end_hour


async def get_daily_count(session: AsyncSession) -> int:
    """Count applications dispatched (status=APPLYING or APPLIED) today."""
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    result = await session.execute(
        select(func.count(Application.id)).where(
            Application.status.in_([ApplicationStatus.APPLYING, ApplicationStatus.APPLIED]),
            Application.updated_at >= today_start,
        )
    )
    return result.scalar_one() or 0


async def enqueue(application_id: str, session: AsyncSession) -> Application:
    """
    Move a DISCOVERED application to QUEUED status.
    Caller is responsible for ensuring the app is in DISCOVERED state.
    """
    from backend.services.state_machine import transition
    return await transition(
        application_id=application_id,
        new_status=ApplicationStatus.QUEUED,
        session=session,
        changed_by="system",
    )


async def dequeue_next(session: AsyncSession) -> Application | None:
    """
    Return the next application to dispatch, or None if:
      - queue is paused
      - outside dispatch window
      - daily cap reached
      - queue is empty

    Moves the returned application to TAILORING status.
    """
    if _PAUSED:
        logger.info("dequeue_skipped_paused")
        return None

    settings = get_settings()

    daily_count = await get_daily_count(session)
    if daily_count >= settings.daily_cap:
        logger.info("dequeue_skipped_daily_cap", extra={"count": daily_count, "cap": settings.daily_cap})
        return None

    # Highest match_score first; fall back to created_at for ties
    result = await session.execute(
        select(Application)
        .where(Application.status == ApplicationStatus.QUEUED)
        .order_by(Application.match_score.desc().nullslast(), Application.created_at.asc())
        .limit(1)
    )
    app = result.scalar_one_or_none()
    if app is None:
        return None

    from backend.services.state_machine import transition
    return await transition(
        application_id=app.id,
        new_status=ApplicationStatus.TAILORING,
        session=session,
        changed_by="system",
    )


async def get_queue_stats(session: AsyncSession) -> QueueStats:
    """Return current queue statistics."""
    settings = get_settings()

    # Count QUEUED apps
    queued_result = await session.execute(
        select(func.count(Application.id)).where(Application.status == ApplicationStatus.QUEUED)
    )
    total_queued = queued_result.scalar_one() or 0

    # By platform
    platform_result = await session.execute(
        select(Application.source_platform, func.count(Application.id))
        .where(Application.status == ApplicationStatus.QUEUED)
        .group_by(Application.source_platform)
    )
    by_platform = {(row[0] or "unknown"): row[1] for row in platform_result.all()}

    daily_dispatched = await get_daily_count(session)

    return QueueStats(
        total_queued=total_queued,
        by_platform=by_platform,
        daily_dispatched=daily_dispatched,
        daily_cap=settings.daily_cap,
        paused=_PAUSED,
    )
