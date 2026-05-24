# Relay — Design Spec
**Date:** 2026-05-24  
**Author:** Aryan Ganju  
**Status:** Approved

---

## What We're Building

**Relay** is a locally-run, autonomous job application system with a web dashboard. It discovers job listings, parses them with an LLM, tailors the user's resume using only existing experience (zero fabrication), handles browser-based form submission, and tracks every application through its full lifecycle.

Full detailed specification lives in `PROMPT.md` at the repo root. This document captures architecture decisions, build strategy, and anything not explicit in the spec.

---

## User

**Aryan Ganju** — Final-year NUS CS undergraduate (AI + Database Systems), targeting backend/ML engineering roles in Singapore.

- Email: aryanganju01@gmail.com
- Phone: +65 8940 9011
- LinkedIn: linkedin.com/in/aryan-ganju
- GitHub: github.com/AryanG01
- Website: aryanganju.vercel.app
- Location: Singapore

---

## Core Constraints (Non-Negotiable)

1. **Zero fabrication** — resume tailoring may rephrase/reorder but never introduces new claims
2. **Human-in-the-loop** — any field with < 0.85 confidence is queued for human review; partial apps never submitted
3. **Graceful degradation** — every handler falls back to assisted mode (visible browser) rather than crashing
4. **Idempotent operations** — re-running any pipeline stage on the same input produces the same output
5. **Full audit trail** — every state transition, form fill, and LLM call is logged with timestamp and inputs

---

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI (async) |
| Browser automation | Playwright (async) + playwright-stealth |
| LLM | Anthropic SDK — claude-sonnet-4-20250514 |
| Database | SQLite via SQLAlchemy async + aiosqlite |
| Task scheduling | APScheduler (AsyncIOScheduler) |
| Resume rendering | WeasyPrint (HTML→PDF) + python-docx fallback |
| Frontend | React 18 + Vite + TailwindCSS + Zustand + TanStack Query |
| Fuzzy matching | rapidfuzz |
| Config | Pydantic Settings + YAML + .env for secrets |

**Why WeasyPrint over pdflatex:** Saves ~600MB on the Oracle VM. HTML/CSS produces print-quality PDFs fully sufficient for a resume. pdflatex is overkill.

**Why SQLite + WAL:** Zero infrastructure, file-based, WAL pragma gives concurrent read performance. SQLAlchemy async makes a future Postgres migration a one-line change.

---

## Architecture

### Deployment: Split Automation

| Component | Runs on | Reason |
|---|---|---|
| FastAPI backend | Oracle Cloud VM (ARM, 4 cores, 24GB RAM) | Always-on, free |
| SQLite DB | Oracle Cloud VM | Persistent storage |
| APScheduler (discovery, parsing, scoring) | Oracle Cloud VM | Cloud scraping is fine |
| React dashboard | Served from Oracle Cloud VM | Accessible from anywhere |
| Playwright form submission | Local machine | Residential IP avoids datacenter flagging |

The local automation agent (`scripts/local_agent.py`) polls `POST /api/automation/claim` every 5 minutes, runs the Playwright handler locally, and reports back to `POST /api/automation/complete`.

### Data Flow

```
Scrapers → Deduplicator → JD Parser → Red Flag Filter
    → Match Scorer → App Queue
        → Bullet Selector → Keyword Injector → Resume Renderer
            → Confidence Scorer → [autofill | escalate to Pending]
                → Platform Handler → Application Result
                    → State Machine → Stage History
```

### No-Fabrication Enforcement (Two Layers)

1. **Prompt level**: System instruction on every injection call — `"CRITICAL: You may only describe what actually happened. Never introduce a skill, technology, outcome, or experience that is not already described in the original bullet. When in doubt, output SKIP."`
2. **Post-injection verification**: Second LLM call explicitly checks for fabrication; reverts to original bullet and logs if verification fails.

### Confidence Gating

- `>= 0.85` → autofill
- `< 0.85` + required → escalate to `pending_clarifications` table
- `< 0.85` + optional → skip (leave blank)
- Always-escalate patterns (cover letters, "why us", personal statements) → escalate regardless of confidence

---

## Application Lifecycle

```
DISCOVERED → QUEUED → TAILORING → APPLYING → APPLIED
                              ↓               ↓ (stage changes)
                  PENDING_CLARIFICATION    SCREENING → OA → PHONE
                              ↓            → INTERVIEW → OFFER
                           APPLYING        → REJECTED / GHOSTED
                    FAILED → QUEUED (retry)
```

State machine validates every transition; invalid transitions raise `ValueError`. All transitions written to `stage_history`.

---

## Key API Surface

- `GET/PATCH /api/applications` — CRUD + stage updates
- `GET/POST /api/queue` — queue management + pause/resume
- `GET/POST /api/pending` — human review queue
- `GET/PUT /api/answer-bank` — stored answers + fuzzy lookup test
- `GET /api/analytics/*` — funnel, velocity, keyword effectiveness
- `GET/PUT /api/resume` — master resume CRUD + preview tailor
- `POST /api/automation/claim|complete|escalate` — local agent endpoints

---

## Frontend Pages

| Page | Purpose |
|---|---|
| `Kanban.tsx` | Drag-and-drop pipeline board by stage |
| `Pending.tsx` | Human review queue with keyboard shortcuts |
| `Analytics.tsx` | Funnel, velocity, score distribution, keyword effectiveness |
| `ResumeEditor.tsx` | Monaco JSON editor with live validation + tailor preview |
| `Settings.tsx` | Config editor, platform auth status, answer bank CRUD |

---

## Build Strategy

**Approach chosen: Strict Sequential (Phases 1 → 8 as spec'd)**

Phases are already dependency-ordered. Deviating adds coordination overhead for no real gain.

| Phase | Focus | Deliverable |
|---|---|---|
| 1 | Data Foundation | DB tables created, master resume validates |
| 2 | LLM Pipeline | `test_pipeline.py` → tailored PDF |
| 3 | Answer Bank + Clarification | Answer CRUD + test-lookup endpoint |
| 4 | Queue + State Machine | Full app CRUD, state machine rejects invalid transitions |
| 5 | Browser Automation | LinkedIn handler + assisted mode fallback |
| 6 | Scrapers | `force-add` URL → parsed JD → queued |
| 7 | Dashboard Frontend | Full dashboard at localhost:5173 |
| 8 | Integration + Hardening | E2E test, rate limiting, daily scheduler, setup script |

---

## Master Resume Data

Seeded from Aryan's actual resume at `data/master_resume.json`:

**Experience:**
- Mercuria Asia Resources Pte Ltd — Software Engineering Intern (2025-05 → 2025-10)
  - NLP trade validation pipeline (97% accuracy, -80% manual workload)
  - FastAPI microservice with async batch processing (-70% turnaround)
  - Semantic clause library with RAG validation (-35% mismatches)
  - ETL pipelines for fixed-income/derivative data (-40% errors)
- TVS Digital Pte Ltd — Software Engineer Intern (2024-06 → 2024-12)
  - QA data generation pipelines, 300+ unit tests, 95% coverage
  - Viber API integration across 7 international partners
  - AWS deployment optimization (-20% deploy time, -15% compute cost)

**Projects:**
- GUI Murphy (TikTok TechJam 2025) — Top 12/200+ teams
  - Multimodal CV pipeline: YOLO + CLIP + GPT-4 + FastAPI + WebSockets

**Skills:** Python, Java, TypeScript, JavaScript, C, SQL, FastAPI, Node.js, Spring Boot, SQLAlchemy, React, PyTorch, PostgreSQL, MySQL, MongoDB, AWS, Docker, Redis, Kafka, Vercel

---

## Config Defaults (Singapore-targeted)

```yaml
daily_cap: 15
min_match_score: 65
human_approval_above: 80
autofill_confidence_threshold: 0.85
salary_expectations:
  SGD: {min: 70000, max: 95000}
  USD: {min: 80000, max: 120000}
work_authorization:
  SG: true
  US: false
  UK: false
dispatch_window:
  days: [tuesday, wednesday, thursday]
  start_hour: 9
  end_hour: 11
```

---

## Security

- `anthropic_api_key` and `secret_key` via environment variables only (never in YAML/git)
- Token-based auth on all API routes (`Authorization: Bearer <token>`)
- `data/`, `*.db`, `data/resumes/`, `.env` all gitignored
- Dashboard token stored in memory on frontend (not localStorage)

---

## Deployment

- Oracle Cloud Always Free — ARM Ampere A1 (4 OCPU, 24GB RAM, 50GB storage)
- systemd service for auto-restart on crash/reboot (`deploy/relay.service`)
- Nginx reverse proxy + Let's Encrypt SSL (`deploy/nginx.conf`)
- One-shot setup script: `deploy/setup.sh`
- Estimated LLM cost: ~$2–5/month at 15 apps/day

---

## Out of Scope

- Multi-user support
- Email parsing for stage updates (noted in spec as optional — deferred)
- Company site scraper (`company_site_scraper.py` — stub only in Phase 6)
