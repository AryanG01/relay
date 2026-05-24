"""
APScheduler setup for Relay.

Jobs:
  - discovery_job   every 6 hours  — runs all scrapers → pipeline
  - expiry_job      every 1 hour   — expires stale DISCOVERED/QUEUED apps

The scheduler is started inside the FastAPI lifespan so it shares the
same event loop as the async DB session.
"""
from __future__ import annotations

import json
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.utils.logging import get_logger

logger = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _discovery_job() -> None:
    """Scheduled discovery run."""
    try:
        from backend.database import AsyncSessionLocal
        from backend.scrapers.discovery import run_discovery
        from backend.scrapers.indeed_scraper import IndeedScraper
        from backend.scrapers.linkedin_scraper import LinkedInScraper

        resume_path = "data/master_resume.json"
        if not os.path.exists(resume_path):
            logger.warning("scheduler_discovery_skipped_no_resume")
            return

        with open(resume_path) as f:
            resume_data = json.load(f)

        scrapers = [IndeedScraper(), LinkedInScraper()]
        async with AsyncSessionLocal() as session:
            counts = await run_discovery(session, scrapers, resume_data)
            logger.info("scheduled_discovery_done", extra=counts)
    except Exception as exc:
        logger.error("scheduled_discovery_failed", extra={"error": str(exc)})


async def _expiry_job() -> None:
    """Scheduled expiry sweep."""
    try:
        from backend.database import AsyncSessionLocal
        from backend.scrapers.discovery import expire_stale_applications

        async with AsyncSessionLocal() as session:
            n = await expire_stale_applications(session)
            if n:
                logger.info("scheduled_expiry_done", extra={"expired": n})
    except Exception as exc:
        logger.error("scheduled_expiry_failed", extra={"error": str(exc)})


def start_scheduler() -> AsyncIOScheduler:
    """Create, configure, and start the global scheduler. Returns it."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = AsyncIOScheduler()

    _scheduler.add_job(
        _discovery_job,
        trigger=IntervalTrigger(hours=6),
        id="discovery",
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.add_job(
        _expiry_job,
        trigger=IntervalTrigger(hours=1),
        id="expiry",
        replace_existing=True,
        misfire_grace_time=120,
    )

    _scheduler.start()
    logger.info("scheduler_started", extra={"jobs": ["discovery(6h)", "expiry(1h)"]})
    return _scheduler


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
