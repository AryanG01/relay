# Phase 4 — Queue + State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Job deduplication, application state machine with audit trail, priority dispatch queue with daily cap + window enforcement, and REST API for applications and queue management.

**Architecture:** `deduplicator.py` gates incoming URLs via `seen_hashes`. `state_machine.py` validates transitions and writes `StageHistory`. `app_queue.py` wraps queue read/write logic (priority = match_score, daily cap = settings.daily_cap, window = settings.dispatch_*). Two routers expose applications CRUD and queue stats/controls. `main.py` gains both new routers.

**Tech Stack:** FastAPI, SQLAlchemy async, pytest + AsyncClient

---

### Task 1: `services/deduplicator.py`

**Files:**
- Create: `backend/services/deduplicator.py`

**Implementation:** Hash the URL with `content_hash`, check/insert `seen_hashes`.

---

### Task 2: `services/state_machine.py`

**Files:**
- Create: `backend/services/state_machine.py`

**Implementation:** Valid transition map, `can_transition()`, `transition()` that updates Application + inserts StageHistory row.

---

### Task 3: `services/app_queue.py`

**Files:**
- Create: `backend/services/app_queue.py`

**Implementation:** `enqueue()`, `dequeue_next()` (highest match_score among QUEUED, respects daily cap + dispatch window), `get_daily_count()`, `is_dispatch_window()`, `get_queue_stats()`, module-level `_PAUSED` flag with `pause_queue()` / `resume_queue()`.

---

### Task 4: `routers/applications.py`

**Files:**
- Create: `backend/routers/applications.py`

**Routes:**
- `GET /api/applications` — list with optional `?status=` / `?stage=` / `?platform=` filters
- `GET /api/applications/{id}` — single application
- `POST /api/applications` — create manually
- `POST /api/applications/{id}/stage` — update stage
- `POST /api/applications/{id}/retry` — FAILED → QUEUED
- `GET /api/applications/{id}/history` — stage history entries

---

### Task 5: `routers/queue.py`

**Files:**
- Create: `backend/routers/queue.py`

**Routes:**
- `GET  /api/queue/stats`
- `POST /api/queue/pause`
- `POST /api/queue/resume`
- `POST /api/queue/enqueue/{app_id}`

---

### Task 6: Update `backend/main.py`

Register the two new routers.

---

### Task 7: `tests/test_phase4.py` — 15 tests

All DB calls use in-memory SQLite. No mocks needed (pure DB logic).
