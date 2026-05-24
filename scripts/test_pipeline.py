#!/usr/bin/env python3
"""
CLI test for the full LLM pipeline (Phase 2 deliverable).

Usage:
    python3 scripts/test_pipeline.py "paste JD text here"
    python3 scripts/test_pipeline.py --file path/to/jd.txt

Runs: parse_jd → score_match → gap_analyzer → select_bullets
      → inject_keywords → render_resume
Outputs a PDF to data/resumes/ and prints a summary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/job_agent.db")


async def run(jd_text: str) -> None:
    from backend.database import AsyncSessionLocal, init_db
    from backend.schemas import MasterResume
    from backend.services.bullet_selector import select_bullets
    from backend.services.gap_analyzer import analyze_gaps
    from backend.services.jd_parser import parse_jd
    from backend.services.keyword_injector import inject_keywords
    from backend.services.match_scorer import score_match
    from backend.services.resume_renderer import render_resume

    await init_db()

    with open("data/master_resume.json") as f:
        resume = MasterResume.model_validate(json.load(f))

    async with AsyncSessionLocal() as session:
        print("\n[1/6] Parsing job description...")
        parsed_jd = await parse_jd(jd_text, session)
        print(f"      Role level : {parsed_jd.role_level} | Domain: {parsed_jd.domain}")
        print(f"      Required   : {', '.join(parsed_jd.required_skills[:6])}")
        print(f"      Confidence : {parsed_jd.confidence:.2f}")

        print("\n[2/6] Scoring match...")
        match_result = await score_match(resume, parsed_jd)
        print(f"      Score      : {match_result.overall_score}/100")
        print(f"      Strong     : {match_result.strong_matches[:4]}")
        print(f"      Missing    : {match_result.missing_required[:4]}")

        print("\n[3/6] Analysing gaps...")
        gap = analyze_gaps(resume, parsed_jd)
        print(f"      Hard gaps  : {gap.hard_gaps[:4]}")
        print(f"      Covered    : {gap.covered[:4]}")

        print("\n[4/6] Selecting bullets...")
        selected = await select_bullets(resume, parsed_jd, match_result)
        total = sum(len(e.get("bullets", [])) for e in selected.work_experience)
        print(f"      {total} bullets across {len(selected.work_experience)} roles")

        print("\n[5/6] Injecting keywords (zero-fabrication)...")
        final_resume = await inject_keywords(selected, parsed_jd)

        print("\n[6/6] Rendering PDF...")
        app_id = str(uuid.uuid4())[:8]
        pdf_path = await render_resume(final_resume, app_id, session)
        print(f"      Written to : {pdf_path}")

    print(f"\n✅  Pipeline complete.")
    print(f"    Match score   : {match_result.overall_score}/100")
    print(f"    PDF           : {pdf_path}")
    print(f"    Section order : {' → '.join(final_resume.section_order)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay full pipeline CLI test")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("jd_text", nargs="?", help="JD text as a positional string")
    group.add_argument("--file", help="Path to a .txt file containing the JD")
    args = parser.parse_args()

    jd_text = args.jd_text
    if args.file:
        with open(args.file) as f:
            jd_text = f.read()

    if not jd_text or not jd_text.strip():
        print("Error: JD text is empty.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(jd_text))


if __name__ == "__main__":
    main()
