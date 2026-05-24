#!/usr/bin/env python3
"""
Relay Local Agent — runs on your local machine (residential IP).

Polls the Relay API every 5 minutes, claims TAILORING applications,
runs Playwright dispatch, and posts the result back.

Usage:
    python3 scripts/local_agent.py
    python3 scripts/local_agent.py --api-url http://your-server:8000
    python3 scripts/local_agent.py --once   # single run, exit after

Environment:
    RELAY_API_URL   Override the API base URL (default: http://localhost:8000)
    DATABASE_URL    Only needed if running dispatch locally against the same DB
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

API_URL = os.getenv("RELAY_API_URL", "http://localhost:8000")
POLL_INTERVAL = 300  # 5 minutes


async def run_once(api_url: str, client: httpx.AsyncClient) -> bool:
    """
    Claim one application, dispatch it, post the result.
    Returns True if an application was processed, False if queue was empty.
    """
    # Check if dispatch is active
    status_resp = await client.get(f"{api_url}/api/automation/status")
    status_resp.raise_for_status()
    status = status_resp.json()

    if not status["enabled"]:
        print("  Dispatch paused — skipping.")
        return False

    if status["daily_dispatched"] >= status["daily_cap"]:
        print(f"  Daily cap reached ({status['daily_dispatched']}/{status['daily_cap']}) — skipping.")
        return False

    # Claim next application
    claim_resp = await client.post(f"{api_url}/api/automation/claim")
    claim_resp.raise_for_status()
    claim = claim_resp.json()

    if not claim.get("application"):
        print(f"  No applications ready: {claim.get('reason', 'unknown')}")
        return False

    app = claim["application"]
    print(f"  Claimed: {app['company']} — {app['role_title']} [{app['id'][:8]}]")

    # Run dispatch locally (Playwright lives here)
    result = await _dispatch_locally(app, api_url)

    # Post result back
    complete_resp = await client.post(
        f"{api_url}/api/automation/complete",
        json={
            "application_id": app["id"],
            "status": result["status"],
            "escalated_fields": result.get("escalated_fields", []),
            "error": result.get("error"),
            "confirmation_url": result.get("confirmation_url"),
        },
    )
    complete_resp.raise_for_status()
    print(f"  Result: {result['status']}")
    if result.get("error"):
        print(f"  Error: {result['error']}")
    return True


async def _dispatch_locally(app: dict, api_url: str) -> dict:
    """
    Run the Playwright handler locally for this application.
    Falls back to assisted mode on any error.
    """
    try:
        # Import dispatch here so Playwright imports stay local
        os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/job_agent.db")
        from backend.database import AsyncSessionLocal, init_db
        from backend.automation.dispatch import dispatch_application

        await init_db()
        async with AsyncSessionLocal() as session:
            return await dispatch_application(app["id"], session)
    except Exception as exc:
        return {"status": "failed", "escalated_fields": [], "error": str(exc)}


async def main(api_url: str, once: bool) -> None:
    print(f"Relay local agent — API: {api_url}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            print(f"\n[{asyncio.get_event_loop().time():.0f}] Checking queue...")
            try:
                await run_once(api_url, client)
            except Exception as exc:
                print(f"  Error: {exc}", file=sys.stderr)

            if once:
                break
            await asyncio.sleep(POLL_INTERVAL)


def cli() -> None:
    parser = argparse.ArgumentParser(description="Relay local dispatch agent")
    parser.add_argument("--api-url", default=API_URL, help="API base URL")
    parser.add_argument("--once", action="store_true", help="Run once then exit")
    args = parser.parse_args()
    asyncio.run(main(args.api_url, args.once))


if __name__ == "__main__":
    cli()
