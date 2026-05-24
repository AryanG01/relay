#!/usr/bin/env python3
"""
One-shot setup script for Relay.

  1. Creates data/ directories
  2. Initialises the SQLite database (creates all tables)
  3. Seeds the answer bank with default values
  4. Verifies master_resume.json is present and valid

Usage:
    python3 scripts/setup.py
    python3 scripts/setup.py --reset   # drop + recreate tables (dev only)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


async def run(reset: bool = False) -> None:
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/job_agent.db")

    from backend.database import AsyncSessionLocal, Base, engine, init_db
    from backend.services.answer_bank import seed_answer_bank

    # 1. Create directories
    for d in ["data", "data/resumes", "data/screenshots"]:
        os.makedirs(d, exist_ok=True)
    print("✓ Directories: data/, data/resumes/, data/screenshots/")

    # 2. Init DB
    if reset:
        print("  ⚠️  Resetting DB tables…")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await init_db()
    print("✓ Database tables created")

    # 3. Seed answer bank
    async with AsyncSessionLocal() as session:
        n = await seed_answer_bank(session)
        print(f"✓ Answer bank seeded ({n} new rows)")

    # 4. Validate master_resume.json
    resume_path = "data/master_resume.json"
    if not os.path.exists(resume_path):
        print(f"⚠️  WARNING: {resume_path} not found — create it before running the pipeline")
        print("   See docs/superpowers/specs/2026-05-24-relay-design.md for the schema")
    else:
        from backend.schemas import MasterResume
        try:
            with open(resume_path) as f:
                MasterResume.model_validate(json.load(f))
            print(f"✓ {resume_path} validated")
        except Exception as exc:
            print(f"✗ {resume_path} is invalid: {exc}", file=sys.stderr)
            sys.exit(1)

    print("\n✅  Setup complete. Start the server with:")
    print("   uvicorn backend.main:app --reload --port 8000")


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay setup")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables (dev only)")
    args = parser.parse_args()
    asyncio.run(run(args.reset))


if __name__ == "__main__":
    main()
