"""
Abstract base class for all job scrapers.

Each scraper is responsible for a single platform and returns
a flat list of JobListing objects.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class JobListing:
    """Raw job listing returned by a scraper before any processing."""
    url: str
    title: str
    company: str
    location: str
    platform: str                    # linkedin | indeed | greenhouse | workday
    raw_jd: str = ""                 # full JD text if available
    salary_text: str = ""            # raw salary string, may be empty
    is_easy_apply: bool = False      # LinkedIn Easy Apply / Indeed Quick Apply
    tags: list[str] = field(default_factory=list)


class BaseScraper(ABC):
    """
    Platform-specific job scraper.

    Subclasses must implement `scrape()`.  All scrapers should be
    defensive — network errors must be caught and logged, never raised.
    """

    platform: str = "unknown"

    @abstractmethod
    async def scrape(
        self,
        query: str,
        location: str = "Singapore",
        max_results: int = 50,
    ) -> list[JobListing]:
        """
        Search for jobs matching the query.

        Args:
            query: Search terms (e.g. "software engineer python").
            location: City or region string.
            max_results: Upper bound on returned listings.

        Returns:
            List of JobListing; empty list on error.
        """
