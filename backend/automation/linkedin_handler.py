"""
LinkedIn Easy Apply handler.

Fills the multi-step Easy Apply modal step-by-step.
Each "Next" click loads a new step — we iterate until Submit or escalation.
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

# Common Easy Apply field selectors
_FIELD_SELECTORS = {
    "phone":            "input[id*='phoneNumber'], input[name*='phone']",
    "linkedin":         "input[id*='linkedIn'], input[name*='linkedin']",
    "resume_upload":    "input[type='file']",
    "cover_letter":     "textarea[id*='coverLetter'], textarea[name*='cover']",
    "years_experience": "input[id*='years'], input[name*='experience']",
    "salary":           "input[id*='salary'], input[name*='salary']",
}

_CONFIRMATION_PATTERNS = [
    "Application submitted",
    "Your application was sent",
    "applied successfully",
    "application received",
]


class LinkedInHandler(BasePlatformHandler):
    platform = "linkedin"

    async def detect_fields(self) -> list[FormField]:
        fields: list[FormField] = []
        try:
            # Scan all visible input/textarea/select elements in the modal
            locators = await self.page.locator(
                ".jobs-easy-apply-modal input, "
                ".jobs-easy-apply-modal textarea, "
                ".jobs-easy-apply-modal select"
            ).all()
            for loc in locators:
                label_text = ""
                try:
                    # Try to find the associated label
                    input_id = await loc.get_attribute("id")
                    if input_id:
                        label_el = self.page.locator(f"label[for='{input_id}']")
                        if await label_el.count() > 0:
                            label_text = (await label_el.text_content() or "").strip()
                except Exception:
                    pass

                tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                field_type = "text"
                if tag == "textarea":
                    field_type = "textarea"
                elif tag == "select":
                    field_type = "dropdown"
                else:
                    input_type = await loc.get_attribute("type") or "text"
                    if input_type in ("checkbox", "radio"):
                        field_type = "checkbox"
                    elif input_type == "number":
                        field_type = "number"
                    elif input_type == "file":
                        field_type = "file"

                required = await loc.get_attribute("required") is not None
                selector = f"#{await loc.get_attribute('id')}" if await loc.get_attribute("id") else ""

                fields.append(FormField(
                    name=label_text.lower().replace(" ", "_") or f"field_{len(fields)}",
                    label=label_text,
                    field_type=field_type,
                    is_required=required,
                    selector=selector,
                ))
        except Exception as exc:
            logger.warning("linkedin_detect_fields_error", extra={"error": str(exc)})
        return fields

    async def fill_form(
        self,
        resume: "SelectedResume",
        parsed_jd: "ParsedJD",
        answers: dict[str, str],
    ) -> FillResult:
        """
        Iterate through Easy Apply steps, filling fields from answers dict.
        Stops and returns FillResult(success=False) if any required field
        has no answer (caller should have pre-resolved via confidence scorer).
        """
        escalated: list[str] = []
        step = 0
        max_steps = 10

        try:
            while step < max_steps:
                step += 1
                fields = await self.detect_fields()

                for f in fields:
                    if f.field_type == "file":
                        continue  # resume upload handled externally
                    answer = answers.get(f.name) or answers.get(f.label.lower().replace(" ", "_"))
                    if not answer:
                        if f.is_required:
                            escalated.append(f.name)
                        continue
                    if f.selector:
                        if f.field_type == "dropdown":
                            await self.safe_select(f.selector, answer)
                        else:
                            await self.safe_fill(f.selector, answer)

                # Check for confirmation
                if await self.detect_confirmation():
                    return FillResult(
                        success=True,
                        submitted=True,
                        escalated_fields=escalated,
                        confirmation_url=self.page.url,
                    )

                # Check if we have required fields still unresolved
                if escalated:
                    return FillResult(
                        success=False,
                        submitted=False,
                        escalated_fields=escalated,
                        error="Required fields missing answers",
                    )

                # Try to advance to next step
                next_btn = self.page.locator(
                    "button[aria-label='Continue to next step'], "
                    "button[aria-label='Review your application'], "
                    "button[aria-label='Submit application']"
                )
                if await next_btn.count() > 0:
                    await next_btn.first.click()
                    await asyncio.sleep(0.8)
                else:
                    break

        except Exception as exc:
            logger.error("linkedin_fill_error", extra={"error": str(exc)})
            return FillResult(success=False, submitted=False, error=str(exc))

        return FillResult(
            success=True,
            submitted=await self.detect_confirmation(),
            escalated_fields=escalated,
        )

    async def detect_confirmation(self) -> bool:
        try:
            content = await self.page.content()
            return any(p.lower() in content.lower() for p in _CONFIRMATION_PATTERNS)
        except Exception:
            return False
