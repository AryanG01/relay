"""
Assisted mode fallback.

When the automated handler fails or encounters an unresolvable field,
we launch a visible (non-headless) browser, pre-fill what we can, and
copy the remaining fields to the system clipboard so the human can
finish the application manually.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from backend.utils.logging import get_logger

if TYPE_CHECKING:
    from backend.schemas import ParsedJD, SelectedResume

logger = get_logger(__name__)


async def launch_assisted(
    url: str,
    resume: "SelectedResume",
    parsed_jd: "ParsedJD",
    answers: dict[str, str],
    escalated_fields: list[str],
) -> None:
    """
    Open a visible Chromium window at the application URL.
    Copies a JSON summary of unanswered fields to the clipboard
    so the user can paste answers directly.

    This is a best-effort helper — never raises, only logs.
    """
    try:
        from playwright.async_api import async_playwright

        clipboard_data = {
            "url": url,
            "company": parsed_jd.raw_keywords[:3],  # rough company hint
            "escalated_fields": escalated_fields,
            "known_answers": {
                k: v for k, v in answers.items()
                if k not in escalated_fields
            },
            "personal": resume.personal,
        }
        clipboard_str = json.dumps(clipboard_data, indent=2)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30_000)

            # Copy escalated field summary to clipboard via JS
            await page.evaluate(
                "text => navigator.clipboard.writeText(text).catch(() => {})",
                clipboard_str,
            )

            logger.info(
                "assisted_mode_launched",
                extra={"url": url, "escalated": escalated_fields},
            )
            # Keep the browser open — user takes over
            # (In production this blocks the coroutine; caller should run
            #  this in a separate thread / background task)
            await page.wait_for_timeout(300_000)  # 5-minute window

    except Exception as exc:
        logger.error("assisted_mode_failed", extra={"error": str(exc)})
