"""
Indeed job scraper using httpx.

Scrapes the Indeed search results page. Indeed aggressively rate-limits
datacenter IPs; this scraper is intended to run on the local machine.

Falls back gracefully on any error — never raises.
"""
from __future__ import annotations

import re

import httpx

from backend.scrapers.base_scraper import BaseScraper, JobListing
from backend.utils.logging import get_logger

logger = get_logger(__name__)

INDEED_BASE = "https://sg.indeed.com/jobs"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-SG,en;q=0.9",
}


def _parse_jobs_from_html(html: str, base_url: str) -> list[JobListing]:
    """
    Extract job listings from Indeed search results HTML.
    Uses regex because importing BeautifulSoup would add a dependency;
    callers that need richer parsing can switch to bs4 here.
    """
    results: list[JobListing] = []

    # Indeed embeds job data as JSON in a <script> tag
    json_matches = re.findall(
        r'"jobKey":"([^"]+)"[^}]*?"title":"([^"]+)"[^}]*?"company":"([^"]+)"',
        html,
    )
    for job_key, title, company in json_matches:
        url = f"https://sg.indeed.com/viewjob?jk={job_key}"
        results.append(JobListing(
            url=url,
            title=title,
            company=company,
            location="Singapore",
            platform="indeed",
            is_easy_apply=True,
        ))

    return results


class IndeedScraper(BaseScraper):
    platform = "indeed"

    async def scrape(
        self,
        query: str,
        location: str = "Singapore",
        max_results: int = 50,
    ) -> list[JobListing]:
        results: list[JobListing] = []
        try:
            params = {
                "q": query,
                "l": location,
                "sort": "date",
                "limit": min(max_results, 50),
            }
            async with httpx.AsyncClient(headers=_HEADERS, timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(INDEED_BASE, params=params)
                resp.raise_for_status()
                results = _parse_jobs_from_html(resp.text, INDEED_BASE)[:max_results]

        except Exception as exc:
            logger.error("indeed_scrape_failed", extra={"error": str(exc)})

        logger.info("indeed_scraped", extra={"count": len(results), "query": query})
        return results
