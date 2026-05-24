"""
URL deduplication via seen_hashes table.

Uses SHA-256 of the normalised URL as the primary key.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import SeenHash
from backend.utils.hashing import content_hash
from backend.utils.logging import get_logger

logger = get_logger(__name__)


def _normalise_url(url: str) -> str:
    """Strip trailing slashes and lowercase scheme+host for stable hashing."""
    return url.strip().rstrip("/").lower()


async def is_duplicate(url: str, session: AsyncSession) -> bool:
    """Return True if this URL has been seen before."""
    h = content_hash(_normalise_url(url))
    result = await session.execute(
        select(SeenHash).where(SeenHash.content_hash == h)
    )
    return result.scalar_one_or_none() is not None


async def mark_seen(
    url: str,
    session: AsyncSession,
    company: str = "",
    role_title: str = "",
) -> str:
    """
    Record a URL as seen.  Returns the content_hash.
    Safe to call even if already seen (idempotent).
    """
    h = content_hash(_normalise_url(url))
    existing = await session.execute(
        select(SeenHash).where(SeenHash.content_hash == h)
    )
    if existing.scalar_one_or_none() is None:
        session.add(SeenHash(
            content_hash=h,
            company=company,
            role_title=role_title,
        ))
        await session.commit()
        logger.info("url_marked_seen", extra={"hash": h[:12], "company": company})
    return h
