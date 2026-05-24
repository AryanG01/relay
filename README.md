# Relay 🛰️

> Your always-on job application relay station. Discovers jobs, tailors your resume (zero fabrication), handles form submission, and tracks every application through its lifecycle.

[![Phase 1](https://img.shields.io/badge/Phase%201-Data%20Foundation%20%E2%9C%93-brightgreen)](plan/) [![Phase 2](https://img.shields.io/badge/Phase%202-LLM%20Pipeline%20%E2%9C%93-brightgreen)](plan/) [![Phase 3](https://img.shields.io/badge/Phase%203-Answer%20Bank%20%E2%9C%93-brightgreen)](plan/) [![Phase 4](https://img.shields.io/badge/Phase%204-Queue%20%26%20State%20Machine%20%E2%9C%93-brightgreen)](plan/) [![Phase 5](https://img.shields.io/badge/Phase%205-Browser%20Automation%20%E2%9C%93-brightgreen)](plan/) [![Phase 6](https://img.shields.io/badge/Phase%206-Scrapers%20%E2%9C%93-brightgreen)](plan/) [![Next](https://img.shields.io/badge/Next-Phase%207%20Dashboard-blue)](plan/)
[![Stack](https://img.shields.io/badge/Stack-Python%20%7C%20FastAPI%20%7C%20React-green)](backend/)
[![Deploy](https://img.shields.io/badge/Deploy-Oracle%20Cloud%20ARM-orange)](deploy/)

---

## What It Does

Relay is a locally-run, autonomous job application system with a web dashboard. It:

1. **Discovers** job listings from LinkedIn, Indeed, and company sites
2. **Parses** JDs with Claude to extract required skills, role level, culture signals
3. **Scores** your resume against each JD (0–100 match score)
4. **Tailors** your resume — selecting and reordering bullets by relevance, injecting JD keywords (zero fabrication enforced at prompt + verification layers)
5. **Applies** via browser automation (Playwright) with anti-detection stealth
6. **Escalates** any field it can't answer with ≥ 0.85 confidence to a human review queue
7. **Tracks** every application through the full lifecycle on a Kanban dashboard

---

## System Architecture

```mermaid
graph TB
    subgraph Oracle Cloud VM ["☁️ Oracle Cloud ARM VM (Always Free)"]
        API[FastAPI Backend :8000]
        DB[(SQLite + WAL)]
        SCHED[APScheduler]
        REACT[React Dashboard\nServed as Static]
        
        SCHED -->|every 6h| DISC[Discovery Pipeline]
        SCHED -->|every 5min| DISPATCH[Dispatch Check]
        DISC --> DB
        DISPATCH --> DB
        API <--> DB
        REACT --> API
    end

    subgraph Local Machine ["💻 Your Local Machine (Residential IP)"]
        AGENT[local_agent.py\nPolls every 5min]
        PW[Playwright Browser]
        AGENT --> PW
    end

    subgraph Platforms ["🌐 Job Platforms"]
        LI[LinkedIn Easy Apply]
        IN[Indeed Quick Apply]
        GH[Greenhouse / Lever]
        WD[Workday]
    end

    subgraph LLM ["🤖 Anthropic Claude"]
        JDP[JD Parser]
        MS[Match Scorer]
        BS[Bullet Selector]
        KI[Keyword Injector]
    end

    AGENT -->|POST /api/automation/claim| API
    PW --> LI & IN & GH & WD
    AGENT -->|POST /api/automation/complete| API
    API <--> LLM
    
    style Oracle Cloud VM fill:#f0f7ff,stroke:#2563eb
    style Local Machine fill:#f0fdf4,stroke:#16a34a
    style LLM fill:#fef9f0,stroke:#d97706
```

---

## Data Flow Pipeline

```mermaid
flowchart LR
    A[🔍 Scrapers] -->|raw job listings| B[Deduplicator\nhash-based]
    B -->|new jobs only| C[Red Flag Filter\nexcluded companies\nvisa requirements]
    C -->|clean listings| D[JD Parser\nClaude LLM]
    D -->|ParsedJD| E[Match Scorer\nkeyword + semantic]
    E -->|score ≥ 65| F[App Queue\nSQLite priority queue]
    E -->|score < 65| X1[❌ Skipped]
    
    F -->|dequeued| G[Bullet Selector\nper-bullet LLM scoring]
    G --> H[Keyword Injector\nno-fabrication enforced]
    H --> I[Resume Renderer\nWeasyPrint PDF]
    I --> J[Confidence Scorer\nper field decision]
    
    J -->|≥ 0.85 confidence| K[Platform Handler\nPlaywright]
    J -->|< 0.85 required| L[⏳ Pending Queue\nhuman review]
    L -->|resolved| K
    
    K -->|success| M[✅ Applied\nStage tracking]
    K -->|error| N[Assisted Mode\nvisible browser]
    K -->|crash| O[❌ Failed → retry]
    
    style A fill:#dbeafe
    style F fill:#dcfce7
    style L fill:#fef9c3
    style M fill:#d1fae5
    style X1 fill:#fee2e2
    style O fill:#fee2e2
```

---

## Application Lifecycle

```mermaid
stateDiagram-v2
    [*] --> DISCOVERED: job found by scraper
    DISCOVERED --> QUEUED: score ≥ 65, no red flags
    DISCOVERED --> SKIPPED: red flag / low score
    DISCOVERED --> EXPIRED: stale after 14 days
    
    QUEUED --> TAILORING: dequeued for dispatch
    QUEUED --> SKIPPED: manual skip
    QUEUED --> EXPIRED: stale after 14 days
    
    TAILORING --> APPLYING: all fields resolved
    TAILORING --> PENDING_CLARIFICATION: required field < 0.85 confidence
    TAILORING --> FAILED: LLM error / render failure
    
    PENDING_CLARIFICATION --> APPLYING: human resolves fields
    PENDING_CLARIFICATION --> SKIPPED: human rejects app
    
    APPLYING --> APPLIED: confirmation detected
    APPLYING --> FAILED: handler error
    
    FAILED --> QUEUED: manual retry
    
    APPLIED --> APPLIED: stage updates\n(Screening / OA / Phone\n/ Interview / Offer\n/ Rejected / Ghosted)
```

---

## Database Schema

```mermaid
erDiagram
    applications {
        text id PK
        text company
        text role_title
        text source_url
        text source_platform
        text jd_hash FK
        text resume_version_id FK
        text status
        text stage
        real match_score
        timestamp applied_at
        timestamp created_at
    }
    
    stage_history {
        int id PK
        text application_id FK
        text from_stage
        text to_stage
        text from_status
        text to_status
        timestamp changed_at
        text changed_by
    }
    
    jd_cache {
        text content_hash PK
        text raw_text
        text parsed_json
        real parse_confidence
        timestamp created_at
    }
    
    resume_versions {
        text id PK
        text application_id FK
        text tailored_json
        text pdf_path
        text render_hash
        timestamp created_at
    }
    
    answer_bank {
        int id PK
        text key
        text value
        text format_hint
        text country_tag
        int usage_count
    }
    
    pending_clarifications {
        int id PK
        text application_id FK
        text field_name
        text suggested_answer
        real confidence
        text status
    }
    
    seen_hashes {
        text content_hash PK
        text company
        text role_title
        timestamp first_seen
    }

    applications ||--o{ stage_history : "has history"
    applications ||--o| jd_cache : "references"
    applications ||--o| resume_versions : "uses"
    applications ||--o{ pending_clarifications : "has"
```

---

## Build Progress

```mermaid
gantt
    title Relay — Build Phases
    dateFormat YYYY-MM-DD
    section Foundation
    Phase 1 · Data Foundation       :active, p1, 2026-05-24, 2d
    Phase 2 · LLM Pipeline          :p2, after p1, 3d
    Phase 3 · Answer Bank           :p3, after p2, 2d
    Phase 4 · Queue + State Machine :p4, after p3, 2d
    section Automation
    Phase 5 · Browser Automation    :p5, after p4, 4d
    Phase 6 · Scrapers              :p6, after p5, 3d
    section Frontend
    Phase 7 · Dashboard             :p7, after p6, 4d
    section Hardening
    Phase 8 · Integration           :p8, after p7, 2d
```

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy async |
| Database | SQLite + WAL mode via aiosqlite |
| LLM | Anthropic Claude (claude-sonnet-4-20250514) |
| Browser | Playwright async + playwright-stealth |
| Resume | WeasyPrint (HTML→PDF) + python-docx fallback |
| Frontend | React 18, Vite, TailwindCSS, Zustand, TanStack Query |
| Scheduling | APScheduler (AsyncIOScheduler) |
| Deployment | Oracle Cloud ARM Always Free |

---

## Quick Start

```bash
# Clone and set up
git clone https://github.com/AryanG01/relay.git
cd relay

# Python environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Configure secrets
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY and SECRET_KEY

# Initialize database + seed answer bank
python scripts/setup.py

# Start backend
uvicorn backend.main:app --reload --port 8000

# In another terminal — start frontend (dev)
cd frontend && npm install && npm run dev
```

---

## Project Structure

```
relay/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Pydantic Settings
│   ├── database.py          # Async SQLAlchemy engine
│   ├── models.py            # ORM models
│   ├── schemas.py           # Pydantic schemas
│   ├── services/            # Business logic
│   ├── automation/          # Playwright handlers
│   ├── scrapers/            # Job discovery
│   ├── routers/             # FastAPI route handlers
│   └── utils/               # LLM client, hashing, logging
├── frontend/                # React + Vite dashboard
├── data/                    # SQLite DB, resume JSON, config (gitignored)
├── templates/               # Resume HTML template
├── scripts/                 # setup.py, local_agent.py, test_pipeline.py
├── deploy/                  # systemd service, nginx config, setup.sh
├── plan/                    # Implementation plans (current phase)
└── tests/                   # pytest test suite
```

---

## Deployment

Runs perpetually at **~$0/month** on Oracle Cloud Always Free (ARM Ampere A1 — 4 cores, 24GB RAM).  
Playwright form submission runs on your local machine (residential IP) to avoid datacenter detection.

See [`deploy/`](deploy/) for systemd service, nginx config, and one-shot setup script.

---

## Core Constraints

- **Zero fabrication** — keyword injector may rephrase bullets but never introduces new claims; enforced at prompt + post-injection verification layers
- **Human-in-the-loop** — fields below 0.85 confidence route to Pending queue; partial applications never submitted
- **Graceful degradation** — every handler falls back to assisted mode (visible browser + clipboard pre-load)
- **Idempotent** — re-running any pipeline stage on the same input produces the same output
- **Full audit trail** — every state transition, form fill, and LLM call logged with timestamp
