# Phase 5 — Browser Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Playwright-based form submission with per-platform handlers, assisted mode fallback, confidence-gated field filling, and a local_agent.py polling script.

**Architecture:** `BasePlatformHandler` ABC defines the contract. Each platform subclass implements `fill_form()`. `dispatch.py` orchestrates: pick handler by platform, call confidence scorer per field, fill or escalate, detect confirmation. `local_agent.py` polls the API every 5 min and calls dispatch. All LLM and browser calls are mockable; unit tests only test the dispatcher logic.

**Tech Stack:** Playwright async, playwright-stealth, FastAPI (automation router), pytest + AsyncMock

---

### Files
- Create: `backend/automation/__init__.py`
- Create: `backend/automation/base_handler.py`
- Create: `backend/automation/linkedin_handler.py`
- Create: `backend/automation/greenhouse_handler.py`
- Create: `backend/automation/assisted_mode.py`
- Create: `backend/automation/dispatch.py`
- Create: `backend/routers/automation.py`
- Create: `scripts/local_agent.py`
- Modify: `backend/main.py`
- Create: `tests/test_phase5.py`
