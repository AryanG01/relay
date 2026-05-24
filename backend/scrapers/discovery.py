"""
Discovery pipeline orchestrator.

Steps for each scraped listing:
  1. Deduplication (seen_hashes)
  2. Red-flag filter
  3. Create Application row (DISCOVERED)
  4. Parse JD with LLM
  5. Score against master resume
  6. score >= min_match_score → transition to QUEUED
  7. score < min_match_score → transition to SKIPPED

Also runs an expiry sweep: DISCOVERED/QUEUED apps older than 14 days → EXPIRED.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import Application, ApplicationStatus
from backend.scrapers.base_scraper import BaseScraper, JobListing
from backend.scrapers.red_flag_detector import detect_red_flags
from backend.services.deduplicator import is_duplicate, mark_seen
from backend.services.jd_parser import parse_jd
from backend.services.match_scorer import score_match
from backend.services.state_machine import transition
from backend.utils.logging import get_logger

logger = get_logger(__name__)

EXPIRY_DAYS = 14


async def process_listing(
    listing: JobListing,
    resume_data: dict,
    session: AsyncSession,
) -> str:
    """
    Process a single job listing through the full pipeline.

    Returns one of: "duplicate" | "red_flag" | "queued" | "skipped" | "error"
    """
    settings = get_settings()

    # 1. Dedup
    if await is_duplicate(listing.url, session):
        return "duplicate"

    # 2. Red-flag filter
    rf = detect_red_flags(listing)
    if rf.is_red_flag:
        logger.info("listing_red_flagged", extra={
            "url": listing.url[:80],
            "reasons": rf.reasons[:2],
        })
        await mark_seen(listing.url, session, listing.company, listing.title)
        return "red_flag"

    # 3. Create Application
    app = Application(
        company=listing.company,
        role_title=listing.title,
        source_url=listing.url,
        source_platform=listing.platform,
        jd_raw=listing.raw_jd or f"{listing.title} at {listing.company} in {listing.location}",
        status=ApplicationStatus.DISCOVERED,
    )
    session.add(app)
    await session.flush()  # get the id without full commit

    # 4. Parse JD
    try:
        from backend.schemas import MasterResume
        master_resume = MasterResume.model_validate(resume_data)
        jd_text = listing.raw_jd or f"{listing.title} at {listing.company}"
        parsed_jd = await parse_jd(jd_text, session)

        # 5. Score
        match_result = await score_match(master_resume, parsed_jd)
        app.match_score = match_result.overall_score

        await session.commit()

        # 6/7. Queue or skip based on score
        if match_result.overall_score >= settings.min_match_score:
            await transition(app.id, ApplicationStatus.QUEUED, session)
            await mark_seen(listing.url, session, listing.company, listing.title)
            logger.info("listing_queued", extra={
                "company": listing.company,
                "score": match_result.overall_score,
            })
            return "queued"
        else:
            await transition(app.id, ApplicationStatus.SKIPPED, session)
            await mark_seen(listing.url, session, listing.company, listing.title)
            logger.info("listing_skipped_low_score", extra={
                "company": listing.company,
                "score": match_result.overall_score,
                "threshold": settings.min_match_score,
            })
            return "skipped"

    except Exception as exc:
        logger.error("listing_pipeline_error", extra={
            "url": listing.url[:80],
            "error": str(exc),
        })
        try:
            await session.rollback()
        except Exception:
            pass
        return "error"


async def run_discovery(
    session: AsyncSession,
    scrapers: list[BaseScraper],
    resume_data: dict,
    *,
    query: str = "software engineer",
    location: str = "Singapore",
    max_per_scraper: int = 50,
) -> dict:
    """
    Run all registered scrapers and process listings through the full pipeline.

    Returns a summary dict with counts per outcome.
    """
    settings = get_settings()
    counts: dict[str, int] = {
        "scraped": 0,
        "duplicate": 0,
        "red_flag": 0,
        "queued": 0,
        "skipped": 0,
        "error": 0,
    }

    for scraper in scrapers:
        try:
            listings = await scraper.scrape(query, location, max_per_scraper)
            counts["scraped"] += len(listings)
            for listing in listings:
                outcome = await process_listing(listing, resume_data, session)
                counts[outcome] = counts.get(outcome, 0) + 1
        except Exception as exc:
            logger.error("scraper_failed", extra={
                "platform": scraper.platform,
                "error": str(exc),
            })

    logger.info("discovery_complete", extra=counts)
    return counts


async def expire_stale_applications(session: AsyncSession) -> int:
    """
    Move DISCOVERED and QUEUED applications older than EXPIRY_DAYS to EXPIRED.
    Returns count of expired applications.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=EXPIRY_DAYS)
    result = await session.execute(
        select(Application).where(
            Application.status.in_([ApplicationStatus.DISCOVERED, ApplicationStatus.QUEUED]),
            Application.created_at < cutoff,
        )
    )
    stale = result.scalars().all()
    for app in stale:
        try:
            await transition(app.id, ApplicationStatus.EXPIRED, session)
        except Exception:
            pass

    if stale:
        logger.info("apps_expired", extra={"count": len(stale)})
    return len(stale)
