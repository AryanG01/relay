# Phase 1 — Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the full directory structure, implement async SQLAlchemy models, Pydantic schemas, config loading, seed master_resume.json with Aryan's real data, and verify everything with pytest.

**Architecture:** Async SQLAlchemy 2.0 with aiosqlite driver. All models inherit from a single `DeclarativeBase`. Pydantic schemas live separately in `schemas.py` — they are NOT the ORM models. SQLite runs in WAL mode for concurrent read performance.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 async, aiosqlite, Pydantic v2, pydantic-settings, PyYAML, pytest, pytest-asyncio

**Deliverable:** `python -c "from backend.database import init_db; import asyncio; asyncio.run(init_db())"` creates all tables without error. `pytest tests/test_phase1.py` passes.

---

### Task 1: Directory Structure + Dependencies

**Files:**
- Create: `backend/__init__.py`
- Create: `backend/services/__init__.py`
- Create: `backend/automation/__init__.py`
- Create: `backend/scrapers/__init__.py`
- Create: `backend/routers/__init__.py`
- Create: `backend/utils/__init__.py`
- Create: `tests/__init__.py`
- Create: `scripts/.gitkeep`
- Create: `data/.gitkeep`
- Create: `templates/.gitkeep`
- Create: `deploy/.gitkeep`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`

- [ ] **Step 1: Create all package directories and init files**

```bash
mkdir -p backend/services backend/automation backend/scrapers backend/routers backend/utils
mkdir -p tests scripts data templates deploy frontend plan
touch backend/__init__.py backend/services/__init__.py backend/automation/__init__.py
touch backend/scrapers/__init__.py backend/routers/__init__.py backend/utils/__init__.py
touch tests/__init__.py
```

- [ ] **Step 2: Write `requirements.txt`**

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
sqlalchemy[asyncio]>=2.0.0
aiosqlite>=0.20.0
pydantic>=2.7.0
pydantic-settings>=2.2.0
anthropic>=0.28.0
playwright>=1.44.0
weasyprint>=62.0
python-docx>=1.1.0
jinja2>=3.1.0
rapidfuzz>=3.9.0
apscheduler>=3.10.0
httpx>=0.27.0
aiofiles>=23.2.0
python-dotenv>=1.0.0
pyyaml>=6.0.0
pytest>=8.2.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Write `.gitignore`**

```
# Secrets and data
.env
data/*.db
data/resumes/
data/screenshots/

# Python
__pycache__/
*.py[cod]
*.egg-info/
venv/
.venv/

# Frontend
frontend/node_modules/
frontend/dist/

# OS
.DS_Store

# LaTeX/render artifacts
*.aux
*.log
*.tex
```

- [ ] **Step 4: Write `.env.example`**

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
SECRET_KEY=change-me-in-production
DATABASE_URL=sqlite+aiosqlite:///data/job_agent.db
```

- [ ] **Step 5: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore .env.example backend/ tests/ scripts/ templates/ deploy/ plan/
git commit -m "chore: scaffold directory structure and dependencies"
```

---

### Task 2: `backend/database.py` — Async Engine + Session Factory

**Files:**
- Create: `backend/database.py`

- [ ] **Step 1: Write `backend/database.py`**

```python
"""
Async SQLAlchemy engine, session factory, and database initialisation.

Uses aiosqlite as the async driver for SQLite.
WAL mode is enabled on every connection for concurrent read performance.
"""
from __future__ import annotations

import os

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite+aiosqlite:///data/job_agent.db"
)

engine = create_async_engine(DATABASE_URL, echo=False)


# SQLite performance pragmas applied on every new connection.
# sync_engine exposes the underlying synchronous engine that aiosqlite wraps.
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-64000")  # 64 MB page cache
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this."""


async def get_session():
    """FastAPI dependency: yields an AsyncSession, auto-closed on exit."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables defined on Base.metadata. Safe to run multiple times."""
    # Import models here so their table definitions are registered on Base.metadata
    import backend.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

- [ ] **Step 2: Commit**

```bash
git add backend/database.py
git commit -m "feat: async SQLAlchemy engine with WAL pragmas"
```

---

### Task 3: `backend/models.py` — All ORM Models

**Files:**
- Create: `backend/models.py`

- [ ] **Step 1: Write `backend/models.py`**

```python
"""
SQLAlchemy ORM models. All tables mirror the schema in PROMPT.md exactly.

Enums are defined as Python str-enums so they serialise cleanly to JSON
and can be used both here and in Pydantic schemas without duplication.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ApplicationStatus(str, Enum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    TAILORING = "tailoring"
    PENDING_CLARIFICATION = "pending_clarification"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    EXPIRED = "expired"
    SKIPPED = "skipped"


class ApplicationStage(str, Enum):
    NONE = "none"
    APPLIED = "applied"
    SCREENING = "screening"
    OA = "oa"
    PHONE = "phone"
    INTERVIEW_1 = "interview_1"
    INTERVIEW_2 = "interview_2"
    INTERVIEW_3 = "interview_3"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    WITHDRAWN = "withdrawn"


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    company: Mapped[str] = mapped_column(String, nullable=False)
    role_title: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String)
    source_platform: Mapped[Optional[str]] = mapped_column(String)
    jd_hash: Mapped[Optional[str]] = mapped_column(String)
    jd_raw: Mapped[Optional[str]] = mapped_column(Text)
    resume_version_id: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=ApplicationStatus.DISCOVERED
    )
    stage: Mapped[str] = mapped_column(
        String, nullable=False, default=ApplicationStage.NONE
    )
    match_score: Mapped[Optional[float]] = mapped_column(Float)
    applied_at: Mapped[Optional[DateTime]] = mapped_column(DateTime)
    created_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_assisted: Mapped[int] = mapped_column(Integer, default=0)
    is_confirmed: Mapped[int] = mapped_column(Integer, default=0)
    apply_mode: Mapped[Optional[str]] = mapped_column(String)


class StageHistory(Base):
    __tablename__ = "stage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(String, nullable=False)
    from_stage: Mapped[Optional[str]] = mapped_column(String)
    to_stage: Mapped[str] = mapped_column(String, nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    changed_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now()
    )
    changed_by: Mapped[str] = mapped_column(String, nullable=False, default="system")
    notes: Mapped[Optional[str]] = mapped_column(Text)


class JDCache(Base):
    __tablename__ = "jd_cache"

    content_hash: Mapped[str] = mapped_column(String, primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[Optional[str]] = mapped_column(Text)
    parse_confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now()
    )


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    application_id: Mapped[Optional[str]] = mapped_column(String)
    tailored_json: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_path: Mapped[Optional[str]] = mapped_column(String)
    docx_path: Mapped[Optional[str]] = mapped_column(String)
    render_hash: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now()
    )


class AnswerBank(Base):
    __tablename__ = "answer_bank"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    format_hint: Mapped[Optional[str]] = mapped_column(String)
    country_tag: Mapped[Optional[str]] = mapped_column(String)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PendingClarification(Base):
    __tablename__ = "pending_clarifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(String, nullable=False)
    field_name: Mapped[str] = mapped_column(String, nullable=False)
    field_label: Mapped[Optional[str]] = mapped_column(String)
    question_text: Mapped[Optional[str]] = mapped_column(Text)
    suggested_answer: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String, default="pending")
    resolved_answer: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[Optional[DateTime]] = mapped_column(DateTime)
    created_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now()
    )


class SeenHash(Base):
    __tablename__ = "seen_hashes"

    content_hash: Mapped[str] = mapped_column(String, primary_key=True)
    company: Mapped[Optional[str]] = mapped_column(String)
    role_title: Mapped[Optional[str]] = mapped_column(String)
    first_seen: Mapped[Optional[DateTime]] = mapped_column(
        DateTime, server_default=func.now()
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/models.py
git commit -m "feat: SQLAlchemy ORM models for all 7 tables"
```

---

### Task 4: `backend/schemas.py` — Pydantic Models

**Files:**
- Create: `backend/schemas.py`

- [ ] **Step 1: Write `backend/schemas.py`**

```python
"""
Pydantic v2 schemas for:
  - MasterResume (source of truth stored in data/master_resume.json)
  - ParsedJD (LLM output from jd_parser)
  - MatchResult (from match_scorer)
  - SelectedResume (tailored resume for a specific application)
  - API request/response models

These are separate from ORM models. ORM models describe DB rows;
schemas describe data contracts at API and service boundaries.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field

from backend.models import ApplicationStage, ApplicationStatus


# ---------------------------------------------------------------------------
# Master Resume Schema
# ---------------------------------------------------------------------------

class PersonalInfo(BaseModel):
    name: str
    email: str
    phone: str
    location: str
    linkedin: str
    github: str
    website: Optional[str] = None


class ResumeBullet(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    skills: list[str] = []
    domain: str = "other"
    action_verb: str = ""
    has_metric: bool = False
    impact_score: float = 0.5
    callback_count: int = 0
    application_count: int = 0


class WorkExperience(BaseModel):
    role: str
    company: str
    location: str
    start_date: str  # YYYY-MM
    end_date: Optional[str] = None
    is_current: bool = False
    bullets: list[ResumeBullet] = []


class Education(BaseModel):
    degree: str
    field: str
    institution: str
    location: str
    start_date: str  # YYYY-MM
    end_date: Optional[str] = None
    gpa: Optional[str] = None
    honors: Optional[str] = None
    relevant_coursework: list[str] = []


class Skills(BaseModel):
    languages: list[str] = []
    frameworks: list[str] = []
    tools: list[str] = []
    databases: list[str] = []
    domains: list[str] = []
    other: list[str] = []


class Project(BaseModel):
    name: str
    description: str
    tech_stack: list[str] = []
    bullets: list[ResumeBullet] = []
    url: Optional[str] = None


class Certification(BaseModel):
    name: str
    issuer: str
    date: str  # YYYY-MM
    url: Optional[str] = None


class MasterResume(BaseModel):
    personal: PersonalInfo
    summary: Optional[str] = None
    work_experience: list[WorkExperience] = []
    education: list[Education] = []
    skills: Skills
    projects: list[Project] = []
    certifications: list[Certification] = []


# ---------------------------------------------------------------------------
# LLM Pipeline Schemas
# ---------------------------------------------------------------------------

class ParsedJD(BaseModel):
    required_skills: list[str]
    preferred_skills: list[str]
    responsibilities: list[str]
    tech_stack: list[str]
    role_level: str  # junior | mid | senior | lead | unknown
    domain: str  # finance | trading | software | data | infra | other
    years_experience_min: Optional[int] = None
    years_experience_max: Optional[int] = None
    culture_signals: list[str] = []
    red_flags: list[str] = []
    sponsorship_available: Optional[bool] = None
    remote_type: str = "unknown"  # remote | hybrid | onsite | unknown
    confidence: float = 1.0
    raw_keywords: list[str] = []


class MatchResult(BaseModel):
    overall_score: float  # 0–100
    required_coverage: float
    experience_relevance: float
    domain_alignment: float
    seniority_fit: float
    missing_required: list[str] = []
    partial_required: list[str] = []
    strong_matches: list[str] = []


class SelectedResume(BaseModel):
    """Tailored resume for a specific application — bullets filtered and reordered."""
    personal: dict
    summary: Optional[str] = None
    work_experience: list[dict] = []
    education: list[dict] = []
    skills: dict = {}
    projects: list[dict] = []
    certifications: list[dict] = []
    section_order: list[str] = Field(
        default=["work_experience", "projects", "skills", "education"]
    )


# ---------------------------------------------------------------------------
# API Request / Response Schemas
# ---------------------------------------------------------------------------

class ApplicationCreate(BaseModel):
    url: str
    company: str
    role_title: str
    notes: Optional[str] = None


class ApplicationStageUpdate(BaseModel):
    stage: ApplicationStage
    notes: Optional[str] = None


class ApplicationResponse(BaseModel):
    id: str
    company: str
    role_title: str
    source_url: Optional[str]
    source_platform: Optional[str]
    status: str
    stage: str
    match_score: Optional[float]
    notes: Optional[str]
    is_assisted: int
    apply_mode: Optional[str]

    model_config = {"from_attributes": True}


class AnswerBankCreate(BaseModel):
    key: str
    value: str
    format_hint: Optional[str] = None
    country_tag: Optional[str] = None


class AnswerBankResponse(BaseModel):
    id: int
    key: str
    value: str
    format_hint: Optional[str]
    country_tag: Optional[str]
    usage_count: int

    model_config = {"from_attributes": True}


class PendingClarificationResponse(BaseModel):
    id: int
    application_id: str
    field_name: str
    field_label: Optional[str]
    question_text: Optional[str]
    suggested_answer: Optional[str]
    confidence: Optional[float]
    status: str

    model_config = {"from_attributes": True}


class ResolveField(BaseModel):
    answer: str
    save_to_bank: bool = False


class QueueStats(BaseModel):
    total_queued: int
    by_platform: dict[str, int]
    daily_dispatched: int
    daily_cap: int
    paused: bool
```

- [ ] **Step 2: Commit**

```bash
git add backend/schemas.py
git commit -m "feat: Pydantic v2 schemas — MasterResume, ParsedJD, MatchResult, API models"
```

---

### Task 5: `backend/config.py` — Settings Loader

**Files:**
- Create: `backend/config.py`

- [ ] **Step 1: Write `backend/config.py`**

```python
"""
Settings loaded from two sources (in priority order):
  1. Environment variables / .env file  ← secrets only (API keys)
  2. data/config.yaml                   ← everything else (limits, windows, filters)

Pydantic-settings merges both; env vars always win on conflict.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    # --- Secrets (must come from env / .env, never from config.yaml) ---
    anthropic_api_key: str = ""
    secret_key: str = "change-me-in-production"

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///data/job_agent.db"

    # --- LLM ---
    llm_model: str = "claude-sonnet-4-20250514"

    # --- Application limits ---
    daily_cap: int = 15
    min_match_score: float = 65.0
    human_approval_above: float = 80.0
    autofill_confidence_threshold: float = 0.85

    # --- Rate limiting ---
    action_delay_mean: float = 0.5
    action_delay_stddev: float = 0.2

    # --- Dispatch window ---
    dispatch_days: list[str] = ["tuesday", "wednesday", "thursday"]
    dispatch_start_hour: int = 9
    dispatch_end_hour: int = 11

    # --- Salary expectations ---
    salary_sgd_min: int = 70000
    salary_sgd_max: int = 95000
    salary_usd_min: int = 80000
    salary_usd_max: int = 120000

    # --- Work authorisation ---
    work_auth_sg: bool = True
    work_auth_us: bool = False
    work_auth_uk: bool = False

    # --- Filters ---
    require_sponsorship: bool = False
    min_role_level: str = "junior"
    exclude_contract_only: bool = False

    # --- Template ---
    resume_template: str = "templates/resume.html"

    # --- Per-platform caps ---
    cap_linkedin: int = 10
    cap_indeed: int = 8
    cap_greenhouse: int = 5
    cap_workday: int = 3

    @property
    def per_platform_caps(self) -> dict[str, int]:
        return {
            "linkedin": self.cap_linkedin,
            "indeed": self.cap_indeed,
            "greenhouse": self.cap_greenhouse,
            "workday": self.cap_workday,
        }


def _load_yaml_settings() -> dict:
    """Read data/config.yaml and flatten nested keys for Pydantic."""
    config_path = os.getenv("CONFIG_PATH", "data/config.yaml")
    if not os.path.exists(config_path):
        return {}
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    flat: dict = {}

    # Top-level scalar values
    for k, v in raw.items():
        if not isinstance(v, dict):
            flat[k] = v

    # dispatch_window nested
    if "dispatch_window" in raw:
        dw = raw["dispatch_window"]
        flat["dispatch_days"] = dw.get("days", flat.get("dispatch_days"))
        flat["dispatch_start_hour"] = dw.get("start_hour", flat.get("dispatch_start_hour"))
        flat["dispatch_end_hour"] = dw.get("end_hour", flat.get("dispatch_end_hour"))

    # salary_expectations nested
    if "salary_expectations" in raw:
        se = raw["salary_expectations"]
        if "SGD" in se:
            flat["salary_sgd_min"] = se["SGD"].get("min", flat.get("salary_sgd_min"))
            flat["salary_sgd_max"] = se["SGD"].get("max", flat.get("salary_sgd_max"))
        if "USD" in se:
            flat["salary_usd_min"] = se["USD"].get("min", flat.get("salary_usd_min"))
            flat["salary_usd_max"] = se["USD"].get("max", flat.get("salary_usd_max"))

    # work_authorization nested
    if "work_authorization" in raw:
        wa = raw["work_authorization"]
        flat["work_auth_sg"] = wa.get("SG", flat.get("work_auth_sg"))
        flat["work_auth_us"] = wa.get("US", flat.get("work_auth_us"))
        flat["work_auth_uk"] = wa.get("UK", flat.get("work_auth_uk"))

    # per_platform_caps nested
    if "per_platform_caps" in raw:
        pc = raw["per_platform_caps"]
        flat["cap_linkedin"] = pc.get("linkedin", flat.get("cap_linkedin"))
        flat["cap_indeed"] = pc.get("indeed", flat.get("cap_indeed"))
        flat["cap_greenhouse"] = pc.get("greenhouse", flat.get("cap_greenhouse"))
        flat["cap_workday"] = pc.get("workday", flat.get("cap_workday"))

    return flat


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance. Call invalidate_settings_cache() in tests."""
    yaml_overrides = _load_yaml_settings()
    return Settings(**yaml_overrides)


def invalidate_settings_cache() -> None:
    """Clear the settings cache — used in tests to reload with different config."""
    get_settings.cache_clear()
```

- [ ] **Step 2: Commit**

```bash
git add backend/config.py
git commit -m "feat: Pydantic settings with YAML + env var merging"
```

---

### Task 6: `data/config.yaml` — Default Configuration

**Files:**
- Create: `data/config.yaml`

- [ ] **Step 1: Write `data/config.yaml`**

```yaml
# Relay Configuration
# Secrets (anthropic_api_key, secret_key) go in .env — never here.

llm_model: "claude-sonnet-4-20250514"

# Application limits
daily_cap: 15
per_platform_caps:
  linkedin: 10
  indeed: 8
  greenhouse: 5
  workday: 3

# Dispatch window (24h local time, days of week)
dispatch_window:
  days: ["tuesday", "wednesday", "thursday"]
  start_hour: 9
  end_hour: 11

# Match scoring thresholds
min_match_score: 65
human_approval_above: 80

# Confidence threshold for autofill vs escalation
autofill_confidence_threshold: 0.85

# Anti-detection timing (seconds, gaussian distribution)
action_delay_mean: 0.5
action_delay_stddev: 0.2

# Salary expectations
salary_expectations:
  SGD:
    min: 70000
    max: 95000
  USD:
    min: 80000
    max: 120000

# Hard exclusion filters
excluded_companies: []
excluded_industries: []
require_sponsorship: false

work_authorization:
  SG: true
  US: false
  UK: false

# Soft filters
min_role_level: "junior"
exclude_contract_only: false

# Resume template
resume_template: "templates/resume.html"

# Scrapers
scrapers:
  linkedin:
    enabled: true
    search_keywords: ["software engineer", "backend developer", "machine learning engineer"]
    location: "Singapore"
    easy_apply_only: false
  indeed:
    enabled: true
    search_keywords: ["software engineer", "backend engineer"]
    location: "Singapore, SG"
  company_sites: []
```

- [ ] **Step 2: Commit**

```bash
git add data/config.yaml
git commit -m "feat: default config.yaml for Singapore-targeted job search"
```

---

### Task 7: `data/master_resume.json` — Aryan's Real Resume

**Files:**
- Create: `data/master_resume.json`

- [ ] **Step 1: Write `data/master_resume.json`**

```json
{
  "personal": {
    "name": "Aryan Ganju",
    "email": "aryanganju01@gmail.com",
    "phone": "+65 8940 9011",
    "location": "Singapore",
    "linkedin": "linkedin.com/in/aryan-ganju",
    "github": "github.com/AryanG01",
    "website": "aryanganju.vercel.app"
  },
  "summary": "Computer Science undergraduate at National University of Singapore specializing in Artificial Intelligence and Database Systems. Delivered production NLP pipelines and scalable backend services in commodity trading and product environments. Targeting backend or machine learning engineering roles focused on distributed systems, data platforms, and AI-assisted automation.",
  "work_experience": [
    {
      "role": "Software Engineering Intern",
      "company": "Mercuria Asia Resources Pte Ltd",
      "location": "Singapore",
      "start_date": "2025-05",
      "end_date": "2025-10",
      "is_current": false,
      "bullets": [
        {
          "id": "merc-001",
          "text": "Engineered an AI / NLP trade validation pipeline reconciling unstructured trade inputs with structured datasets, achieving 97% accuracy and cutting manual workload by 80% across Middle Office workflows.",
          "skills": ["Python", "NLP", "FastAPI", "Machine Learning"],
          "domain": "finance",
          "action_verb": "Engineered",
          "has_metric": true,
          "impact_score": 0.92,
          "callback_count": 0,
          "application_count": 0
        },
        {
          "id": "merc-002",
          "text": "Shipped solution as a FastAPI microservice with asynchronous batch processing, cutting validation turnaround time by 70% within enterprise trading infrastructure.",
          "skills": ["FastAPI", "Python", "async", "microservices"],
          "domain": "finance",
          "action_verb": "Shipped",
          "has_metric": true,
          "impact_score": 0.88,
          "callback_count": 0,
          "application_count": 0
        },
        {
          "id": "merc-003",
          "text": "Designed a semantic clause library using normalization and Retrieval-Augmented Generation (RAG) validation, lowering recurring mismatches by 35% while strengthening contract consistency across commodity trades.",
          "skills": ["RAG", "NLP", "Python", "LLM"],
          "domain": "finance",
          "action_verb": "Designed",
          "has_metric": true,
          "impact_score": 0.85,
          "callback_count": 0,
          "application_count": 0
        },
        {
          "id": "merc-004",
          "text": "Developed internal ETL pipelines transforming raw fixed-income and derivative trade data into structured analyst-ready datasets, decreasing interpretation errors by 40% and accelerating reconciliation workflows.",
          "skills": ["ETL", "Python", "SQL", "data engineering"],
          "domain": "finance",
          "action_verb": "Developed",
          "has_metric": true,
          "impact_score": 0.82,
          "callback_count": 0,
          "application_count": 0
        }
      ]
    },
    {
      "role": "Software Engineer Intern",
      "company": "TVS Digital Pte Ltd",
      "location": "Singapore",
      "start_date": "2024-06",
      "end_date": "2024-12",
      "is_current": false,
      "bullets": [
        {
          "id": "tvs-001",
          "text": "Implemented automated QA data generation pipelines using Python and MySQL and introduced test-driven development with 300+ unit tests, raising coverage to 95%.",
          "skills": ["Python", "MySQL", "TDD", "pytest"],
          "domain": "engineering",
          "action_verb": "Implemented",
          "has_metric": true,
          "impact_score": 0.78,
          "callback_count": 0,
          "application_count": 0
        },
        {
          "id": "tvs-002",
          "text": "Led end-to-end Viber API integration across seven international partners, enabling reliable production messaging at scale through structured testing and edge-case validation.",
          "skills": ["API integration", "Python", "testing"],
          "domain": "engineering",
          "action_verb": "Led",
          "has_metric": true,
          "impact_score": 0.75,
          "callback_count": 0,
          "application_count": 0
        },
        {
          "id": "tvs-003",
          "text": "Optimized AWS deployment workflows through monitoring and automation scripts, cutting deployment time by 20% and lowering compute costs by 15%.",
          "skills": ["AWS", "DevOps", "Python", "automation"],
          "domain": "ops",
          "action_verb": "Optimized",
          "has_metric": true,
          "impact_score": 0.72,
          "callback_count": 0,
          "application_count": 0
        }
      ]
    }
  ],
  "education": [
    {
      "degree": "Bachelor of Computing",
      "field": "Computer Science",
      "institution": "National University of Singapore",
      "location": "Singapore",
      "start_date": "2022-08",
      "end_date": "2026-05",
      "gpa": null,
      "honors": "Specializations: Artificial Intelligence, Database Systems",
      "relevant_coursework": [
        "Distributed Systems",
        "Machine Learning",
        "Database Systems",
        "Computer Vision",
        "Algorithms & Data Structures"
      ]
    }
  ],
  "skills": {
    "languages": ["Python", "Java", "TypeScript", "JavaScript", "C", "SQL"],
    "frameworks": ["FastAPI", "Node.js", "Spring Boot", "SQLAlchemy", "React"],
    "tools": ["Docker", "Git", "AWS", "Redis", "Kafka", "Vercel", "Playwright"],
    "databases": ["PostgreSQL", "MySQL", "MongoDB", "SQLite"],
    "domains": ["NLP", "Machine Learning", "RAG", "Computer Vision", "ETL", "Distributed Systems"],
    "other": ["REST APIs", "Microservices", "TDD", "PyTorch", "WebSockets"]
  },
  "projects": [
    {
      "name": "GUI Murphy",
      "description": "AI-driven GUI testing assistant detecting subtle UI regressions (color drift, icon changes, layout inconsistencies) often missed by rule-based automation tools.",
      "tech_stack": ["Python", "FastAPI", "YOLO", "CLIP", "GPT-4", "WebSockets"],
      "bullets": [
        {
          "id": "gui-001",
          "text": "Developed an AI-driven GUI testing assistant detecting subtle UI regressions (color drift, icon changes, layout inconsistencies) often missed by rule-based automation tools.",
          "skills": ["Python", "Computer Vision", "AI"],
          "domain": "engineering",
          "action_verb": "Developed",
          "has_metric": false,
          "impact_score": 0.80,
          "callback_count": 0,
          "application_count": 0
        },
        {
          "id": "gui-002",
          "text": "Architected a multimodal computer-vision reasoning pipeline combining YOLO, CLIP, and GPT-4 with FastAPI and WebSockets for real-time visual comparison and developer feedback.",
          "skills": ["YOLO", "CLIP", "GPT-4", "FastAPI", "WebSockets", "multimodal"],
          "domain": "engineering",
          "action_verb": "Architected",
          "has_metric": false,
          "impact_score": 0.85,
          "callback_count": 0,
          "application_count": 0
        },
        {
          "id": "gui-003",
          "text": "Ranked Top 12 of 200+ teams at TikTok TechJam 2025.",
          "skills": [],
          "domain": "other",
          "action_verb": "Ranked",
          "has_metric": true,
          "impact_score": 0.70,
          "callback_count": 0,
          "application_count": 0
        }
      ],
      "url": "https://github.com/AryanG01/techjam-2025-final"
    }
  ],
  "certifications": []
}
```

- [ ] **Step 2: Note — add data/ to .gitignore exception for config only**

The `.gitignore` already excludes `data/*.db` and `data/resumes/`. The `master_resume.json` and `config.yaml` are intentionally tracked (they contain no secrets — API key is in `.env`).

- [ ] **Step 3: Commit**

```bash
git add data/master_resume.json data/config.yaml
git commit -m "feat: seed master_resume.json with real resume data and config.yaml defaults"
```

---

### Task 8: `tests/test_phase1.py` — Pytest Test Suite

**Files:**
- Create: `tests/test_phase1.py`
- Create: `pytest.ini`

- [ ] **Step 1: Write `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 2: Write `tests/test_phase1.py`**

```python
"""
Phase 1 tests — data foundation.

Tests:
  1. master_resume.json loads and validates against MasterResume schema
  2. config.yaml loads into Settings without error
  3. init_db() creates all 7 tables in a fresh in-memory SQLite DB
  4. All ApplicationStatus and ApplicationStage enum values are non-empty strings
"""
from __future__ import annotations

import json
import os
import pytest

# Use in-memory DB for all tests — never touch data/job_agent.db
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"


def test_master_resume_loads_and_validates():
    """master_resume.json must parse cleanly against the MasterResume Pydantic model."""
    from backend.schemas import MasterResume

    resume_path = "data/master_resume.json"
    assert os.path.exists(resume_path), f"Missing {resume_path}"

    with open(resume_path) as f:
        raw = json.load(f)

    resume = MasterResume.model_validate(raw)

    assert resume.personal.name == "Aryan Ganju"
    assert resume.personal.email == "aryanganju01@gmail.com"
    assert len(resume.work_experience) >= 2
    assert len(resume.education) >= 1
    assert len(resume.skills.languages) > 0
    assert "Python" in resume.skills.languages

    # Every bullet must have an id and non-empty text
    for exp in resume.work_experience:
        for bullet in exp.bullets:
            assert bullet.id, f"Bullet missing id in {exp.company}"
            assert bullet.text.strip(), f"Empty bullet text in {exp.company}"
            assert 0.0 <= bullet.impact_score <= 1.0


def test_config_loads_from_yaml():
    """Settings must load from data/config.yaml with expected defaults."""
    from backend.config import get_settings, invalidate_settings_cache

    invalidate_settings_cache()
    settings = get_settings()

    assert settings.daily_cap == 15
    assert settings.min_match_score == 65.0
    assert settings.autofill_confidence_threshold == 0.85
    assert settings.work_auth_sg is True
    assert settings.work_auth_us is False
    assert settings.salary_sgd_min == 70000
    assert settings.salary_sgd_max == 95000
    assert settings.per_platform_caps["linkedin"] == 10


@pytest.mark.asyncio
async def test_init_db_creates_all_tables():
    """init_db() must create all 7 expected tables in a fresh DB."""
    from sqlalchemy import inspect, text
    from backend.database import engine, init_db

    await init_db()

    expected_tables = {
        "applications",
        "stage_history",
        "jd_cache",
        "resume_versions",
        "answer_bank",
        "pending_clarifications",
        "seen_hashes",
    }

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        actual_tables = {row[0] for row in result.fetchall()}

    assert expected_tables == actual_tables, (
        f"Table mismatch.\nExpected: {expected_tables}\nGot: {actual_tables}"
    )


def test_application_status_enum_values():
    """All ApplicationStatus values must be non-empty strings."""
    from backend.models import ApplicationStatus

    expected = {
        "discovered", "queued", "tailoring", "pending_clarification",
        "applying", "applied", "failed", "expired", "skipped",
    }
    actual = {s.value for s in ApplicationStatus}
    assert actual == expected


def test_application_stage_enum_values():
    """All ApplicationStage values must be non-empty strings."""
    from backend.models import ApplicationStage

    expected = {
        "none", "applied", "screening", "oa", "phone",
        "interview_1", "interview_2", "interview_3",
        "offer", "rejected", "ghosted", "withdrawn",
    }
    actual = {s.value for s in ApplicationStage}
    assert actual == expected


def test_parsed_jd_schema_defaults():
    """ParsedJD must accept minimal required fields and fill sensible defaults."""
    from backend.schemas import ParsedJD

    jd = ParsedJD(
        required_skills=["Python", "FastAPI"],
        preferred_skills=["Docker"],
        responsibilities=["Build REST APIs"],
        tech_stack=["Python", "FastAPI", "PostgreSQL"],
        role_level="mid",
        domain="software",
    )

    assert jd.remote_type == "unknown"
    assert jd.confidence == 1.0
    assert jd.sponsorship_available is None
    assert jd.years_experience_min is None


def test_match_result_schema():
    """MatchResult must store all sub-scores and categorised skills."""
    from backend.schemas import MatchResult

    result = MatchResult(
        overall_score=78.5,
        required_coverage=0.85,
        experience_relevance=0.80,
        domain_alignment=0.75,
        seniority_fit=0.90,
        missing_required=["Kubernetes"],
        strong_matches=["Python", "FastAPI"],
    )

    assert result.overall_score == 78.5
    assert "Kubernetes" in result.missing_required
    assert result.partial_required == []
```

- [ ] **Step 3: Run tests — verify they pass**

```bash
pytest tests/test_phase1.py -v
```

Expected output:
```
tests/test_phase1.py::test_master_resume_loads_and_validates PASSED
tests/test_phase1.py::test_config_loads_from_yaml PASSED
tests/test_phase1.py::test_init_db_creates_all_tables PASSED
tests/test_phase1.py::test_application_status_enum_values PASSED
tests/test_phase1.py::test_application_stage_enum_values PASSED
tests/test_phase1.py::test_parsed_jd_schema_defaults PASSED
tests/test_phase1.py::test_match_result_schema PASSED

7 passed in X.XXs
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_phase1.py pytest.ini
git commit -m "test: Phase 1 data foundation test suite — 7 tests passing"
```

---

### Task 9: Manual Smoke Test + Final Commit

- [ ] **Step 1: Verify deliverable — init_db() creates all tables**

```bash
python -c "from backend.database import init_db; import asyncio; asyncio.run(init_db()); print('DB initialised OK')"
```

Expected: `DB initialised OK` (and `data/job_agent.db` exists on disk).

- [ ] **Step 2: Update README build progress badge and push**

Update `README.md` line 4:
```markdown
[![Phase](https://img.shields.io/badge/Phase-1%20Complete%20✓-green)](plan/)
```

- [ ] **Step 3: Final push**

```bash
git add README.md
git commit -m "chore: mark Phase 1 complete"
git push origin main
```

---

## Phase 1 Complete ✓

Deliverables:
- `backend/database.py` — async engine, WAL pragmas, `init_db()`, `get_session()`
- `backend/models.py` — 7 ORM tables + `ApplicationStatus` / `ApplicationStage` enums
- `backend/schemas.py` — `MasterResume`, `ParsedJD`, `MatchResult`, `SelectedResume`, all API schemas
- `backend/config.py` — settings merged from YAML + env vars, `lru_cache` cached
- `data/master_resume.json` — Aryan's real resume, schema-validated
- `data/config.yaml` — Singapore-targeted defaults
- `tests/test_phase1.py` — 7 passing pytest tests
- All committed and pushed to `AryanG01/relay`

**Next:** `plan/phase-02-llm-pipeline.md` — `utils/llm.py`, `jd_parser`, `match_scorer`, `bullet_selector`, `keyword_injector`, `resume_renderer`, `test_pipeline.py`
