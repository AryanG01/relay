#!/usr/bin/env python3
"""
Save your LinkedIn session so the scraper can run without logging in every time.

Usage:
    python3 scripts/linkedin_auth.py

A visible Chrome window opens. Log in to LinkedIn normally (including any 2FA).
Once you see your LinkedIn feed, press Enter here and your session is saved to
data/linkedin_auth.json.  The scraper will load this file automatically.

The session file contains your browser cookies — keep it out of git (it is
already in .gitignore).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

AUTH_STATE_PATH = "data/linkedin_auth.json"


async def main() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    os.makedirs("data", exist_ok=True)

    print("Opening LinkedIn login page in a visible browser...")
    print("  → Log in normally (username + password + any 2FA)")
    print("  → Wait until you can see your LinkedIn feed / home page")
    print("  → Then come back here and press Enter\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        ctx = await browser.new_context(
            viewport=None,  # use maximized window
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        # Hand control to the user
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("Press Enter once you're logged in and see your feed... ")
        )

        # Verify we're actually logged in
        current_url = page.url
        if "feed" not in current_url and "mynetwork" not in current_url and "jobs" not in current_url:
            print(f"\n⚠️  You don't appear to be on a logged-in page (URL: {current_url})")
            print("   Make sure you're on your LinkedIn feed before pressing Enter.")
            confirm = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("Save anyway? [y/N] ")
            )
            if confirm.strip().lower() != "y":
                print("Aborted — no session saved.")
                await browser.close()
                return

        await ctx.storage_state(path=AUTH_STATE_PATH)
        await browser.close()

    print(f"\n✅  Session saved to {AUTH_STATE_PATH}")
    print("   The LinkedIn scraper will now use this session automatically.")
    print("   Re-run this script if you get logged out (~30–90 days).")


if __name__ == "__main__":
    asyncio.run(main())
