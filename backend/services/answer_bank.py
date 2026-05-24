"""
Answer bank: store and retrieve answers to application form fields.

Lookup priority:
  1. Exact key match (after normalisation)
  2. Fuzzy match via rapidfuzz.token_sort_ratio >= 75
  3. Inference (years of experience from resume dates)
  4. not_found

Always-escalate patterns bypass the bank entirely — caller must escalate.
"""
from __future__ import annotations

import re

from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AnswerBank
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Regex patterns that always trigger human escalation regardless of confidence
ALWAYS_ESCALATE_PATTERNS: list[str] = [
    r"cover.?letter",
    r"why.+(want|join|interested|excited).+(company|role|position|us)",
    r"tell.+about.+yourself",
    r"motivat",
    r"what.+bring.+to",
    r"personal.+statement",
]

_COMPILED_ESCALATE = [re.compile(p, re.IGNORECASE) for p in ALWAYS_ESCALATE_PATTERNS]

# Pre-seeded keys — populated by seed_answer_bank()
SEED_KEYS: list[dict] = [
    {"key": "salary_expectation_sgd",   "value": "SGD 70,000 - 95,000",                         "format_hint": "range",  "country_tag": "SG"},
    {"key": "salary_expectation_usd",   "value": "USD 80,000 - 120,000",                         "format_hint": "range",  "country_tag": "US"},
    {"key": "graduation_date",           "value": "May 2026",                                     "format_hint": "text",   "country_tag": None},
    {"key": "work_authorization_sg",     "value": "Yes",                                          "format_hint": "yesno",  "country_tag": "SG"},
    {"key": "work_authorization_us",     "value": "No",                                           "format_hint": "yesno",  "country_tag": "US"},
    {"key": "work_authorization_uk",     "value": "No",                                           "format_hint": "yesno",  "country_tag": "UK"},
    {"key": "notice_period_weeks",       "value": "4",                                            "format_hint": "number", "country_tag": None},
    {"key": "years_experience_python",   "value": "3",                                            "format_hint": "number", "country_tag": None},
    {"key": "years_experience_java",     "value": "2",                                            "format_hint": "number", "country_tag": None},
    {"key": "linkedin_url",              "value": "linkedin.com/in/aryan-ganju",                  "format_hint": "text",   "country_tag": None},
    {"key": "github_url",                "value": "github.com/AryanG01",                          "format_hint": "text",   "country_tag": None},
    {"key": "portfolio_url",             "value": "aryanganju.vercel.app",                        "format_hint": "text",   "country_tag": None},
    {"key": "phone_number",              "value": "+65 8940 9011",                                "format_hint": "text",   "country_tag": None},
    {"key": "current_location",          "value": "Singapore",                                    "format_hint": "text",   "country_tag": None},
    {"key": "willing_to_relocate",       "value": "No",                                           "format_hint": "yesno",  "country_tag": None},
    {"key": "preferred_work_type",       "value": "hybrid",                                       "format_hint": "text",   "country_tag": None},
    {"key": "highest_education_level",   "value": "Bachelor's Degree",                            "format_hint": "text",   "country_tag": None},
    {"key": "university_name",           "value": "National University of Singapore",             "format_hint": "text",   "country_tag": None},
    {"key": "degree_name",               "value": "Bachelor of Computing in Computer Science",    "format_hint": "text",   "country_tag": None},
    {"key": "gpa",                       "value": "Not disclosed",                                "format_hint": "text",   "country_tag": None},
    {"key": "employment_status",         "value": "student",                                      "format_hint": "text",   "country_tag": None},
]


class AnswerLookupResult(BaseModel):
    key: str
    value: str
    confidence: float        # 1.0 exact | 0.7–0.99 fuzzy | 0.5–0.69 inferred | 0.0 not_found
    match_type: str          # exact | fuzzy | inferred | not_found
    format_hint: str | None


def _normalise(label: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    label = label.lower()
    label = re.sub(r"[^\w\s]", " ", label)
    return re.sub(r"\s+", " ", label).strip()


def _format_value(value: str, field_type: str, format_hint: str | None) -> str:
    """Reformat a stored value for the specific field_type."""
    if field_type == "number":
        nums = re.findall(r"\d[\d,]*", value.replace(",", ""))
        return nums[0] if nums else value
    if field_type in ("checkbox", "yesno", "boolean"):
        lower = value.lower()
        if lower in ("yes", "true", "1"):
            return "Yes"
        if lower in ("no", "false", "0"):
            return "No"
    return value


def should_always_escalate(field_label: str) -> bool:
    """Return True if the label matches any always-escalate pattern."""
    for pattern in _COMPILED_ESCALATE:
        if pattern.search(field_label):
            return True
    return False


async def lookup_answer(
    field_label: str,
    field_type: str,
    session: AsyncSession,
    context: dict | None = None,
) -> AnswerLookupResult:
    """
    Look up an answer for a form field.

    1. Normalise label.
    2. Exact key match against answer_bank table.
    3. Fuzzy match using token_sort_ratio >= 75.
    4. Infer if possible (years of experience).
    5. Apply country_tag filter if context contains country.
    6. Format value for field_type.
    7. Increment usage_count on match.
    """
    normalised = _normalise(field_label)
    country = (context or {}).get("country")

    result = await session.execute(select(AnswerBank))
    rows: list[AnswerBank] = list(result.scalars().all())

    def _country_ok(row: AnswerBank) -> bool:
        if row.country_tag is None:
            return True
        if country is None:
            return True
        return row.country_tag.upper() == country.upper()

    candidates = [r for r in rows if _country_ok(r)]

    # 1. Exact match
    for row in candidates:
        if _normalise(row.key) == normalised:
            row.usage_count += 1
            await session.commit()
            return AnswerLookupResult(
                key=row.key,
                value=_format_value(row.value, field_type, row.format_hint),
                confidence=1.0,
                match_type="exact",
                format_hint=row.format_hint,
            )

    # 2. Fuzzy match
    best_row: AnswerBank | None = None
    best_score = 0.0
    for row in candidates:
        score = fuzz.token_sort_ratio(_normalise(row.key), normalised) / 100.0
        if score > best_score:
            best_score = score
            best_row = row

    if best_row and best_score >= 0.75:
        best_row.usage_count += 1
        await session.commit()
        # Scale 0.75–1.0 → 0.70–0.99
        confidence = 0.70 + (best_score - 0.75) * (0.29 / 0.25)
        return AnswerLookupResult(
            key=best_row.key,
            value=_format_value(best_row.value, field_type, best_row.format_hint),
            confidence=round(min(0.99, confidence), 3),
            match_type="fuzzy",
            format_hint=best_row.format_hint,
        )

    # 3. Infer years of experience
    yoe_match = re.search(r"years?.+experience|experience.+years?", normalised)
    if yoe_match:
        try:
            yoe_row = next((r for r in rows if r.key == "years_experience_python"), None)
            if yoe_row:
                return AnswerLookupResult(
                    key="inferred_yoe",
                    value=_format_value(yoe_row.value, field_type, "number"),
                    confidence=0.55,
                    match_type="inferred",
                    format_hint="number",
                )
        except Exception:
            pass

    return AnswerLookupResult(
        key=normalised,
        value="",
        confidence=0.0,
        match_type="not_found",
        format_hint=None,
    )


async def seed_answer_bank(session: AsyncSession) -> int:
    """
    Insert pre-seeded answer bank rows if they don't already exist.
    Returns the count of newly inserted rows.
    """
    inserted = 0
    for seed in SEED_KEYS:
        existing = await session.execute(
            select(AnswerBank).where(AnswerBank.key == seed["key"])
        )
        if existing.scalar_one_or_none() is None:
            session.add(AnswerBank(**seed))
            inserted += 1
    await session.commit()
    logger.info("answer_bank_seeded", extra={"inserted": inserted})
    return inserted
