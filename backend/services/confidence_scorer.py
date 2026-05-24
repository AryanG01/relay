"""
Per-field autofill vs escalate decision.

Decision logic:
  1. Always-escalate pattern match → escalate (regardless of confidence)
  2. Answer bank lookup >= autofill_confidence_threshold → autofill
  3. Below threshold + required → escalate
  4. Below threshold + not required → skip
"""
from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.services.answer_bank import lookup_answer, should_always_escalate
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class FieldDecision(BaseModel):
    field_name: str
    field_label: str
    decision: str        # autofill | escalate | skip
    answer: str | None
    confidence: float
    reason: str


async def decide_field(
    field_name: str,
    field_label: str,
    field_type: str,
    is_required: bool,
    session: AsyncSession,
    context: dict | None = None,
) -> FieldDecision:
    """
    Decide whether to autofill, escalate, or skip a form field.

    Args:
        field_name: Internal field identifier (e.g. "salary_expectation").
        field_label: Human-readable label from the form (e.g. "Expected Salary (SGD)").
        field_type: text | dropdown | checkbox | yesno | number
        is_required: Whether the field must be filled.
        session: DB session for answer bank lookup.
        context: Optional dict with keys like 'country', 'company', 'application_id'.
    """
    settings = get_settings()
    threshold = settings.autofill_confidence_threshold

    # 1. Always-escalate check
    if should_always_escalate(field_label):
        logger.info(
            "field_decision_escalate_pattern",
            extra={"field_name": field_name, "app_id": (context or {}).get("application_id")},
        )
        return FieldDecision(
            field_name=field_name,
            field_label=field_label,
            decision="escalate",
            answer=None,
            confidence=0.0,
            reason="Matches always-escalate pattern (personal/motivational content)",
        )

    # 2. Answer bank lookup
    lookup = await lookup_answer(field_label, field_type, session, context)

    if lookup.confidence >= threshold:
        logger.info(
            "field_decision_autofill",
            extra={"field_name": field_name, "match_type": lookup.match_type, "confidence": lookup.confidence},
        )
        return FieldDecision(
            field_name=field_name,
            field_label=field_label,
            decision="autofill",
            answer=lookup.value,
            confidence=lookup.confidence,
            reason=f"{lookup.match_type} match in answer bank",
        )

    # 3. Required but low confidence → escalate
    if is_required:
        logger.info(
            "field_decision_escalate_required",
            extra={"field_name": field_name, "confidence": lookup.confidence},
        )
        return FieldDecision(
            field_name=field_name,
            field_label=field_label,
            decision="escalate",
            answer=lookup.value or None,
            confidence=lookup.confidence,
            reason=f"Required field — confidence {lookup.confidence:.2f} < threshold {threshold}",
        )

    # 4. Optional + low confidence → skip
    logger.info(
        "field_decision_skip",
        extra={"field_name": field_name, "confidence": lookup.confidence},
    )
    return FieldDecision(
        field_name=field_name,
        field_label=field_label,
        decision="skip",
        answer=None,
        confidence=lookup.confidence,
        reason=f"Optional field — confidence {lookup.confidence:.2f} < threshold, leaving blank",
    )
