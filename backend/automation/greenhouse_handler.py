"""
Greenhouse (and Lever) application handler.

These ATSs use a single-page form rather than a modal, making them
simpler to automate. Confirmation is detected via URL change or
a "Thank you" heading.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from backend.automation.base_handler import BasePlatformHandler, FillResult, FormField
from backend.utils.logging import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page
    from backend.schemas import ParsedJD, SelectedResume

logger = get_logger(__name__)

_CONFIRMATION_PATTERNS = [
    "thank you for applying",
    "application submitted",
    "application received",
    "we've received your application",
    "successfully submitted",
]

# Greenhouse standard field ids / names
_GH_FIELDS = {
    "first_name":   "input#first_name",
    "last_name":    "input#last_name",
    "email":        "input#email",
    "phone":        "input#phone",
    "resume":       "input#resume",
    "cover_letter": "textarea#cover_letter",
    "linkedin":     "input[name='job_application[answers_attributes][0][text_value]']",
}


class GreenhouseHandler(BasePlatformHandler):
    platform = "greenhouse"

    async def detect_fields(self) -> list[FormField]:
        fields: list[FormField] = []
        try:
            inputs = await self.page.locator("form#application_form input, form#application_form textarea, form#application_form select").all()
            for loc in inputs:
                field_id = await loc.get_attribute("id") or ""
                name = await loc.get_attribute("name") or field_id
                label_text = ""
                if field_id:
                    lbl = self.page.locator(f"label[for='{field_id}']")
                    if await lbl.count():
                        label_text = (await lbl.text_content() or "").strip()
                tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                field_type = "text"
                if tag == "textarea":
                    field_type = "textarea"
                elif tag == "select":
                    field_type = "dropdown"
                required = await loc.get_attribute("required") is not None
                fields.append(FormField(
                    name=name,
                    label=label_text or name,
                    field_type=field_type,
                    is_required=required,
                    selector=f"#{field_id}" if field_id else f"[name='{name}']",
                ))
        except Exception as exc:
            logger.warning("greenhouse_detect_fields_error", extra={"error": str(exc)})
        return fields

    async def fill_form(
        self,
        resume: "SelectedResume",
        parsed_jd: "ParsedJD",
        answers: dict[str, str],
    ) -> FillResult:
        escalated: list[str] = []
        personal = resume.personal

        # Fill standard fields from resume
        standard = {
            "first_name":   personal.get("name", "").split()[0],
            "last_name":    " ".join(personal.get("name", "").split()[1:]),
            "email":        personal.get("email", ""),
            "phone":        personal.get("phone", ""),
        }

        try:
            for field_name, selector in _GH_FIELDS.items():
                if field_name in ("resume", "cover_letter", "linkedin"):
                    continue
                value = standard.get(field_name) or answers.get(field_name, "")
                if value:
                    await self.safe_fill(selector, value)
                    await asyncio.sleep(0.15)

            # Fill dynamic fields from answers
            fields = await self.detect_fields()
            for f in fields:
                if f.name in standard:
                    continue
                answer = answers.get(f.name) or answers.get(f.label.lower().replace(" ", "_"), "")
                if not answer and f.is_required:
                    escalated.append(f.name)
                    continue
                if answer and f.selector:
                    if f.field_type == "dropdown":
                        await self.safe_select(f.selector, answer)
                    else:
                        await self.safe_fill(f.selector, answer)

            if escalated:
                return FillResult(
                    success=False,
                    submitted=False,
                    escalated_fields=escalated,
                    error="Required fields missing answers",
                )

            # Submit
            submit_btn = self.page.locator("input[type='submit'], button[type='submit']")
            if await submit_btn.count():
                await submit_btn.first.click()
                await asyncio.sleep(2.0)

        except Exception as exc:
            logger.error("greenhouse_fill_error", extra={"error": str(exc)})
            return FillResult(success=False, submitted=False, error=str(exc))

        confirmed = await self.detect_confirmation()
        return FillResult(
            success=confirmed,
            submitted=confirmed,
            escalated_fields=escalated,
            confirmation_url=self.page.url if confirmed else None,
        )

    async def detect_confirmation(self) -> bool:
        try:
            content = await self.page.content()
            return any(p in content.lower() for p in _CONFIRMATION_PATTERNS)
        except Exception:
            return False
