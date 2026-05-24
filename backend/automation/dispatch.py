"""
Application dispatch orchestrator.

Workflow for a single application:
  1. Load application + resume version from DB
  2. Parse JD (cached)
  3. Run confidence scorer on detected form fields
  4. If any required fields escalate → create PendingClarification rows, return
  5. Launch Playwright, pick platform handler, fill form
  6. On success → transition to APPLIED
  7. On failure → transition to FAILED, optionally launch assisted mode
  8. Screenshots stored in data/screenshots/
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

try:
    from playwright.async_api import async_playwright
except ImportError:  # Playwright not installed in test env
    async_playwright = None  # type: ignore[assignment]

from backend.config import get_settings
from backend.models import Application, ApplicationStatus, PendingClarification
from backend.schemas import ParsedJD, SelectedResume
from backend.services.confidence_scorer import decide_field
from backend.services.jd_parser import parse_jd
from backend.services.state_machine import transition
from backend.utils.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

SCREENSHOTS_DIR = Path("data/screenshots")

# Platform → handler class mapping
_HANDLER_REGISTRY: dict[str, type] = {}


def register_handler(platform: str):
    """Decorator to register a platform handler class."""
    def decorator(cls):
        _HANDLER_REGISTRY[platform.lower()] = cls
        return cls
    return decorator


def _get_handler_class(platform: str):
    """Return handler class for platform, or None if not registered."""
    # Lazy-import handlers to avoid Playwright import at test time
    from backend.automation.greenhouse_handler import GreenhouseHandler
    from backend.automation.linkedin_handler import LinkedInHandler
    registry = {
        "linkedin": LinkedInHandler,
        "greenhouse": GreenhouseHandler,
        "lever": GreenhouseHandler,  # Lever uses the same form structure
    }
    return registry.get((platform or "").lower())


async def dispatch_application(
    application_id: str,
    session: AsyncSession,
    *,
    force_assisted: bool = False,
) -> dict:
    """
    Attempt to submit a single application.

    Returns a result dict with keys:
      status: "applied" | "escalated" | "failed" | "assisted"
      escalated_fields: list of field names requiring human input
      error: error message if status=="failed"
    """
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    settings = get_settings()

    app = await session.get(Application, application_id)
    if not app:
        return {"status": "failed", "error": f"Application {application_id!r} not found", "escalated_fields": []}

    # Parse JD
    jd_text = app.jd_raw or f"{app.role_title} at {app.company}"
    parsed_jd: ParsedJD = await parse_jd(jd_text, session)

    # Build answers from confidence scorer
    form_fields_to_check = [
        ("phone",               "Phone Number",               "text",   False),
        ("email",               "Email Address",              "text",   False),
        ("salary_expectation",  "Expected Salary",            "text",   False),
        ("work_authorization",  "Authorized to work?",        "yesno",  True),
        ("linkedin_url",        "LinkedIn Profile URL",       "text",   False),
        ("github_url",          "GitHub Profile URL",         "text",   False),
        ("years_experience",    "Years of Experience",        "number", False),
        ("notice_period",       "Notice Period (weeks)",      "number", False),
        ("current_location",    "Current Location",           "text",   False),
    ]

    answers: dict[str, str] = {}
    escalated_fields: list[str] = []
    pending_to_create: list[dict] = []

    ctx = {"application_id": application_id, "company": app.company}
    for field_name, field_label, field_type, is_required in form_fields_to_check:
        decision = await decide_field(
            field_name=field_name,
            field_label=field_label,
            field_type=field_type,
            is_required=is_required,
            session=session,
            context=ctx,
        )
        if decision.decision == "autofill" and decision.answer:
            answers[field_name] = decision.answer
        elif decision.decision == "escalate":
            escalated_fields.append(field_name)
            pending_to_create.append({
                "application_id": application_id,
                "field_name": field_name,
                "field_label": field_label,
                "question_text": f"Please provide: {field_label}",
                "suggested_answer": decision.answer,
                "confidence": decision.confidence,
                "status": "pending",
            })

    # If required escalations exist → create PendingClarification rows, stop
    required_escalated = [
        f for f in escalated_fields
        if any(fn == f and req for fn, _, _, req in form_fields_to_check)
    ]
    if required_escalated or (pending_to_create and not force_assisted):
        for pc in pending_to_create:
            session.add(PendingClarification(**pc))
        await session.commit()
        await transition(
            application_id=application_id,
            new_status=ApplicationStatus.PENDING_CLARIFICATION,
            session=session,
        )
        logger.info("dispatch_escalated", extra={
            "app_id": application_id,
            "fields": escalated_fields,
        })
        return {
            "status": "escalated",
            "escalated_fields": escalated_fields,
            "error": None,
        }

    if force_assisted:
        from backend.automation.assisted_mode import launch_assisted
        asyncio.create_task(launch_assisted(
            url=app.source_url or "",
            resume=SelectedResume(personal={}),
            parsed_jd=parsed_jd,
            answers=answers,
            escalated_fields=escalated_fields,
        ))
        return {"status": "assisted", "escalated_fields": escalated_fields, "error": None}

    # Transition to APPLYING before launching the browser
    await transition(
        application_id=application_id,
        new_status=ApplicationStatus.APPLYING,
        session=session,
        changed_by="system",
    )

    # Launch Playwright and attempt automated fill
    try:
        handler_cls = _get_handler_class(app.source_platform or "")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                from playwright_stealth import stealth_async
            except ImportError:
                stealth_async = None

            ctx_browser = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = await ctx_browser.new_page()
            if stealth_async:
                await stealth_async(page)

            await page.goto(app.source_url or "", wait_until="networkidle", timeout=30_000)

            # Load resume from last version
            selected_resume = SelectedResume(personal={
                "name": "Aryan Ganju",
                "email": "rajeevganju0@gmail.com",
                "phone": answers.get("phone", "+65 8940 9011"),
            })

            if handler_cls:
                handler = handler_cls(page)
                fill_result = await handler.fill_form(selected_resume, parsed_jd, answers)
            else:
                # Unknown platform — go straight to assisted mode
                fill_result_data = {
                    "success": False,
                    "submitted": False,
                    "assisted": True,
                    "escalated_fields": [],
                    "error": f"No handler for platform {app.source_platform!r}",
                }
                await browser.close()
                return {**fill_result_data, "status": "assisted"}

            # Screenshot regardless of outcome
            screenshot_path = str(SCREENSHOTS_DIR / f"{application_id}.png")
            await handler.take_screenshot(screenshot_path)

            await browser.close()

        if fill_result.submitted:
            app.applied_at = datetime.now(timezone.utc)
            await session.commit()
            await transition(
                application_id=application_id,
                new_status=ApplicationStatus.APPLIED,
                session=session,
            )
            logger.info("dispatch_applied", extra={"app_id": application_id})
            return {
                "status": "applied",
                "escalated_fields": [],
                "error": None,
                "confirmation_url": fill_result.confirmation_url,
                "screenshot_path": screenshot_path,
            }
        else:
            await transition(
                application_id=application_id,
                new_status=ApplicationStatus.FAILED,
                session=session,
            )
            return {
                "status": "failed",
                "escalated_fields": fill_result.escalated_fields,
                "error": fill_result.error,
                "screenshot_path": screenshot_path,
            }

    except Exception as exc:
        logger.error("dispatch_crash", extra={"app_id": application_id, "error": str(exc)})
        try:
            await transition(
                application_id=application_id,
                new_status=ApplicationStatus.FAILED,
                session=session,
                notes=str(exc),
            )
        except Exception:
            pass
        return {"status": "failed", "escalated_fields": [], "error": str(exc)}
