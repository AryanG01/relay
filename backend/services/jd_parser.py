"""
LLM-based job description parser with SQLite cache.

Cache strategy: hash raw JD text → check jd_cache table →
if found with parsed_json: return deserialized ParsedJD.
If not: call LLM → store result → return.
"""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import JDCache
from backend.schemas import ParsedJD
from backend.utils.hashing import content_hash
from backend.utils.llm import call_llm
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_SYSTEM = (
    "You are a precise job description parser. "
    "Extract structured data from the job description. "
    "Return ONLY valid JSON matching the exact schema provided. No markdown, no explanation."
)

_PROMPT_TEMPLATE = """\
Schema:
{{
  "required_skills": ["list of explicitly required skills/technologies"],
  "preferred_skills": ["nice-to-have skills"],
  "responsibilities": ["key responsibilities, max 8, each under 15 words"],
  "tech_stack": ["all mentioned technologies, tools, languages"],
  "role_level": "junior|mid|senior|lead|unknown",
  "domain": "finance|trading|software|data|infra|other",
  "years_experience_min": null,
  "years_experience_max": null,
  "culture_signals": ["list of culture/environment descriptors"],
  "red_flags": ["potential constraints: visa restrictions, relocation, clearance"],
  "sponsorship_available": null,
  "remote_type": "remote|hybrid|onsite|unknown",
  "confidence": 0.0,
  "raw_keywords": ["all significant domain/skill terms found"]
}}

Job Description:
{raw_text}
"""


async def parse_jd(raw_text: str, session: AsyncSession) -> ParsedJD:
    """
    Parse a job description into a structured ParsedJD.

    1. Hash raw_text → check jd_cache.
    2. Cache hit with parsed_json → return cached ParsedJD.
    3. Cache miss → call LLM → store → return.
    """
    jd_hash = content_hash(raw_text)

    # --- Cache lookup ---
    cached = await session.get(JDCache, jd_hash)
    if cached and cached.parsed_json:
        logger.info("jd_cache_hit", extra={"jd_hash": jd_hash[:12]})
        data = json.loads(cached.parsed_json)
        return ParsedJD.model_validate(data)

    # --- LLM call ---
    logger.info("jd_cache_miss", extra={"jd_hash": jd_hash[:12]})
    prompt = _PROMPT_TEMPLATE.format(raw_text=raw_text)
    raw_json = await call_llm(prompt, system=_SYSTEM, max_tokens=2000, expect_json=True)
    data = json.loads(raw_json)
    parsed = ParsedJD.model_validate(data)

    # --- Store in cache ---
    if cached:
        cached.parsed_json = raw_json
        cached.parse_confidence = parsed.confidence
    else:
        session.add(
            JDCache(
                content_hash=jd_hash,
                raw_text=raw_text,
                parsed_json=raw_json,
                parse_confidence=parsed.confidence,
            )
        )
    await session.commit()

    return parsed
