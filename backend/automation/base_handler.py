"""
Abstract base class for all platform-specific form handlers.

Each handler receives a Playwright Page, a SelectedResume, and parsed job context.
It returns a FillResult describing whether the form was submitted or needs escalation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

    from backend.schemas import ParsedJD, SelectedResume


@dataclass
class FormField:
    """A single detected form field."""
    name: str                   # internal identifier / label text
    label: str                  # display label
    field_type: str             # text | textarea | dropdown | checkbox | yesno | number | file
    is_required: bool = False
    selector: str = ""          # CSS / Playwright selector
    options: list[str] = field(default_factory=list)  # for dropdowns


@dataclass
class FillResult:
    """Outcome of a handler's fill_form() call."""
    success: bool
    submitted: bool             # True if confirmation page detected
    assisted: bool = False      # True if fell back to visible browser
    escalated_fields: list[str] = field(default_factory=list)
    error: str | None = None
    confirmation_url: str | None = None
    screenshot_path: str | None = None


class BasePlatformHandler(ABC):
    """
    Platform-specific form handler.

    Subclasses must implement:
      - detect_fields()  → list[FormField]
      - fill_form()      → FillResult
      - detect_confirmation() → bool
    """

    platform: str = "unknown"

    def __init__(self, page: "Page"):
        self.page = page

    @abstractmethod
    async def detect_fields(self) -> list[FormField]:
        """Scan the current page and return detected form fields."""

    @abstractmethod
    async def fill_form(
        self,
        resume: "SelectedResume",
        parsed_jd: "ParsedJD",
        answers: dict[str, str],
    ) -> FillResult:
        """
        Fill the form using the provided answers dict (field_name → value).
        Returns FillResult with submission status.
        """

    @abstractmethod
    async def detect_confirmation(self) -> bool:
        """Return True if the current page indicates a successful submission."""

    async def safe_fill(self, selector: str, value: str) -> bool:
        """
        Try to fill a field; return False on error instead of raising.
        Adds a small random delay to mimic human typing pace.
        """
        import random
        try:
            await self.page.fill(selector, value)
            await self.page.wait_for_timeout(int(random.gauss(300, 80)))
            return True
        except Exception:
            return False

    async def safe_select(self, selector: str, value: str) -> bool:
        """Try to select a dropdown option by label or value."""
        try:
            await self.page.select_option(selector, label=value)
            return True
        except Exception:
            try:
                await self.page.select_option(selector, value=value)
                return True
            except Exception:
                return False

    async def take_screenshot(self, path: str) -> None:
        """Save a screenshot to path for debugging."""
        try:
            await self.page.screenshot(path=path, full_page=True)
        except Exception:
            pass
