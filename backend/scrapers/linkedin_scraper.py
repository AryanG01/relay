"""
LinkedIn job scraper using Playwright.

LinkedIn requires a logged-in session to access full job details.
The scraper loads a saved auth state (data/linkedin_auth.json) if available.

In production this runs on the local machine (residential IP).
In the cloud VM it is disabled — set LINKEDIN_SCRAPER_ENABLED=false.
"""
from __future__ import annotations

import os

from backend.scrapers.base_scraper import BaseScraper, JobListing
from backend.utils.logging import get_logger

logger = get_logger(__name__)

LINKEDIN_BASE = "https://www.linkedin.com/jobs/search"
AUTH_STATE_PATH = "data/linkedin_auth.json"


class LinkedInScraper(BaseScraper):
    platform = "linkedin"

    async def scrape(
        self,
        query: str,
        location: str = "Singapore",
        max_results: int = 50,
    ) -> list[JobListing]:
        if not os.path.exists(AUTH_STATE_PATH):
            logger.warning("linkedin_auth_missing", extra={"path": AUTH_STATE_PATH})
            return []

        results: list[JobListing] = []
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    storage_state=AUTH_STATE_PATH,
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()

                search_url = (
                    f"{LINKEDIN_BASE}?keywords={query.replace(' ', '%20')}"
                    f"&location={location.replace(' ', '%20')}"
                    f"&f_LF=f_AL"  # Easy Apply filter
                )
                await page.goto(search_url, wait_until="networkidle", timeout=30_000)

                # Collect job cards
                cards = await page.locator(".job-search-card").all()
                for card in cards[:max_results]:
                    try:
                        title = (await card.locator(".job-search-card__title").text_content() or "").strip()
                        company = (await card.locator(".job-search-card__company-name").text_content() or "").strip()
                        loc = (await card.locator(".job-search-card__location").text_content() or "").strip()
                        link = await card.locator("a.job-search-card__title-link").get_attribute("href") or ""

                        results.append(JobListing(
                            url=link.split("?")[0],
                            title=title,
                            company=company,
                            location=loc,
                            platform="linkedin",
                            is_easy_apply=True,
                        ))
                    except Exception:
                        continue

                await browser.close()

        except Exception as exc:
            logger.error("linkedin_scrape_failed", extra={"error": str(exc)})

        logger.info("linkedin_scraped", extra={"count": len(results), "query": query})
        return results
