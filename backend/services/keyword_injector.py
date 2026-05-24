"""
Rewrite selected resume bullets to naturally include missing JD keywords.

Zero-fabrication is enforced at two layers:
  1. Injection prompt: explicit SKIP instruction if keyword doesn't fit.
  2. Post-injection verification: second LLM call confirms no new claims were added.
     If verification fails, original bullet is restored and the failure is logged.

Limits: max 2 rewrites per work_experience role, max 1 per project.
"""
from __future__ import annotations

import copy
import json

from pydantic import BaseModel

from backend.schemas import ParsedJD, SelectedResume
from backend.utils.llm import call_llm
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_INJECT_SYSTEM = (
    "You are rewriting resume bullet points. "
    "CRITICAL: You may only describe what actually happened. "
    "Never introduce a skill, technology, outcome, or experience "
    "that is not already described in the original bullet. "
    "When in doubt, output SKIP."
)

_INJECT_PROMPT = """\
Rewrite the resume bullet to naturally include the keyword below.

STRICT RULES:
- Do not introduce any new claim not present in the original.
- Preserve all numbers, percentages, and quantitative metrics exactly.
- Keep the same action verb or use a direct synonym.
- The rewrite must read naturally, not forced.
- If you cannot include the keyword without fabricating, output SKIP.

Original bullet: {original_text}
Keyword to include: {keyword}
Context (why this keyword fits): {context}

Output ONLY the rewritten bullet text, or the single word SKIP.
"""

_VERIFY_SYSTEM = (
    "You are a fabrication detector for resumes. "
    "Return ONLY valid JSON."
)

_VERIFY_PROMPT = """\
Compare these two resume bullets and check for fabrication.

Original: {original}
Rewritten: {rewritten}

Did the rewrite introduce any new claim, skill, technology, or outcome
not present in the original? Answer strictly.

Return JSON:
{{"fabrication_detected": true/false, "reason": "one sentence"}}
"""


class InjectionResult(BaseModel):
    bullet_id: str
    original_text: str
    rewritten_text: str | None
    injected_keywords: list[str]
    was_modified: bool
    skip_reason: str | None


async def _inject_one(
    bullet_id: str, original_text: str, keyword: str, context: str
) -> InjectionResult:
    """Attempt to inject one keyword into one bullet. Verify if modified."""
    prompt = _INJECT_PROMPT.format(
        original_text=original_text, keyword=keyword, context=context
    )
    rewritten = (await call_llm(prompt, system=_INJECT_SYSTEM, max_tokens=300)).strip()

    if rewritten.upper() == "SKIP" or not rewritten:
        return InjectionResult(
            bullet_id=bullet_id,
            original_text=original_text,
            rewritten_text=None,
            injected_keywords=[],
            was_modified=False,
            skip_reason="LLM chose SKIP",
        )

    # --- Post-injection fabrication verification ---
    verify_prompt = _VERIFY_PROMPT.format(original=original_text, rewritten=rewritten)
    raw_verify = await call_llm(
        verify_prompt, system=_VERIFY_SYSTEM, max_tokens=200, expect_json=True
    )
    verdict = json.loads(raw_verify)

    if verdict.get("fabrication_detected", True):
        logger.warning(
            "injection_fabrication_detected",
            extra={
                "bullet_id": bullet_id,
                "keyword": keyword,
                "reason": verdict.get("reason"),
            },
        )
        return InjectionResult(
            bullet_id=bullet_id,
            original_text=original_text,
            rewritten_text=None,
            injected_keywords=[],
            was_modified=False,
            skip_reason=f"Fabrication detected: {verdict.get('reason')}",
        )

    return InjectionResult(
        bullet_id=bullet_id,
        original_text=original_text,
        rewritten_text=rewritten,
        injected_keywords=[keyword],
        was_modified=True,
        skip_reason=None,
    )


async def inject_keywords(
    selected_resume: SelectedResume,
    parsed_jd: ParsedJD,
) -> SelectedResume:
    """
    Inject missing JD keywords into selected resume bullets.
    Returns an updated SelectedResume with rewritten bullets where safe.
    """
    all_text = " ".join(
        b["text"]
        for section in [selected_resume.work_experience, selected_resume.projects]
        for role in section
        for b in role.get("bullets", [])
    ).lower()

    missing_keywords = [
        kw for kw in parsed_jd.required_skills if kw.lower() not in all_text
    ]

    if not missing_keywords:
        logger.info("no_keywords_to_inject")
        return selected_resume

    logger.info("injecting_keywords", extra={"count": len(missing_keywords)})
    resume_copy = copy.deepcopy(selected_resume)

    # Work experience: max 2 rewrites per role
    for role in resume_copy.work_experience:
        rewrites_this_role = 0
        for bullet in role.get("bullets", []):
            if rewrites_this_role >= 2 or not missing_keywords:
                break
            keyword = missing_keywords[0]
            result = await _inject_one(
                bullet_id=bullet["id"],
                original_text=bullet["text"],
                keyword=keyword,
                context=f"Role: {role.get('role')} at {role.get('company')}",
            )
            if result.was_modified and result.rewritten_text:
                bullet["text"] = result.rewritten_text
                missing_keywords.pop(0)
                rewrites_this_role += 1

    # Projects: max 1 rewrite per project
    for proj in resume_copy.projects:
        if not missing_keywords:
            break
        for bullet in proj.get("bullets", [])[:1]:
            keyword = missing_keywords[0]
            result = await _inject_one(
                bullet_id=bullet["id"],
                original_text=bullet["text"],
                keyword=keyword,
                context=f"Project: {proj.get('name')}",
            )
            if result.was_modified and result.rewritten_text:
                bullet["text"] = result.rewritten_text
                missing_keywords.pop(0)
            break

    return resume_copy
