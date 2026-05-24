# Phase 6 — Scrapers + Discovery Pipeline Implementation Plan

**Goal:** Job discovery pipeline: scrapers → dedup → red-flag filter → JD parse → match score → enqueue. APScheduler runs discovery every 6h and expiry sweep every hour.

**Architecture:** `BaseScraper` ABC → `LinkedInScraper` + `IndeedScraper`. `discovery.py` orchestrates all registered scrapers. `red_flag_detector.py` is a pure function. `scheduler.py` wires APScheduler jobs into the FastAPI lifespan. Tests inject a `FakeScraper` stub.

**Files:**
- `backend/scrapers/__init__.py`
- `backend/scrapers/base_scraper.py`
- `backend/scrapers/linkedin_scraper.py`
- `backend/scrapers/indeed_scraper.py`
- `backend/scrapers/red_flag_detector.py`
- `backend/scrapers/discovery.py`
- `backend/services/scheduler.py`
- Modify `backend/main.py`
- `tests/test_phase6.py`
