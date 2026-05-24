"""Content hashing helpers for deduplication and cache keying."""
from __future__ import annotations
import hashlib


def content_hash(text: str) -> str:
    """SHA-256 of text, hex-encoded. Used for JD cache keys and seen_hashes."""
    return hashlib.sha256(text.strip().encode()).hexdigest()


def short_hash(text: str, length: int = 12) -> str:
    """First `length` chars of content_hash — for log labels, never for keys."""
    return content_hash(text)[:length]
