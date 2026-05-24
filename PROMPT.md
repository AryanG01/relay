# Job Application Agent — Full Build Prompt for Claude Code

You are building **JobAgent**: a locally-run, autonomous job application system with a web dashboard. It discovers job listings, parses them, tailors the user's resume using only their existing experience (zero fabrication), handles browser-based form submission, and tracks every application through its lifecycle.

Read this entire document before writing a single line of code. Follow the build phases in order. Ask for clarification on anything ambiguous before proceeding.

---

## Core Constraints (Non-Negotiable)

1. **Zero fabrication**: The resume tailoring engine may rephrase and reorder existing content but must never introduce claims, skills, or experiences not present in the master resume JSON.
2. **Human-in-the-loop**: Any form field the system cannot answer with ≥ 0.85 confidence must be queued for human review. Never submit a partial application.
3. **Graceful degradation**: Every automation handler must fall back to assisted mode (visible browser + clipboard pre-load) rather than crashing or submitting incorrectly.
4. **Idempotent operations**: Re-running any pipeline stage on the same input must produce the same output. No duplicate applications ever.
5. **Full audit trail**: Every state transition, every form fill, every LLM call is logged with timestamp and inputs.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.11+, FastAPI | Async-native, you know it from Mercuria |
| Browser automation | Playwright (async) + playwright-stealth | Best-in-class stealth, async support |
| LLM calls | Anthropic Python SDK (claude-sonnet-4-20250514) | Structured JSON output, fast |
| Database | SQLite via SQLAlchemy (async) | Zero-infra, file-based, upgradeable to PostgreSQL |
| Task scheduling | APScheduler (AsyncIOScheduler) | Lightweight, integrates with FastAPI |
| Resume rendering | LaTeX → pdflatex, python-docx fallback | Pixel-perfect PDF output |
| Frontend | React 18 + Vite + TailwindCSS | Fast dev, you know React |
| Frontend state | Zustand | Simpler than Redux for this use case |
| Frontend HTTP | TanStack Query (react-query) | Auto-refetch, cache, loading states |
| Fuzzy matching | rapidfuzz | Fast string similarity for answer bank |
| Config | Pydantic Settings + YAML config file | Type-safe, human-editable |

---

## Directory Structure

```
job-agent/
├── backend/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Pydantic Settings, loads config.yaml
│   ├── database.py                # SQLAlchemy async engine, session factory
│   ├── models.py                  # SQLAlchemy ORM models
│   ├── schemas.py                 # Pydantic request/response schemas
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── discovery.py           # Job scraping orchestration
│   │   ├── deduplicator.py        # Hash-based dedup logic
│   │   ├── jd_parser.py           # LLM-based JD parsing
│   │   ├── red_flag_detector.py   # Filter logic against user constraints
│   │   ├── match_scorer.py        # Resume vs JD match scoring
│   │   ├── gap_analyzer.py        # Skill gap diff
│   │   ├── bullet_selector.py     # Per-bullet relevance scoring
│   │   ├── keyword_injector.py    # Bullet rewriting with no-fabrication constraint
│   │   ├── resume_renderer.py     # JSON → LaTeX → PDF / python-docx
│   │   ├── answer_bank.py         # Key-value answer store + fuzzy lookup
│   │   ├── confidence_scorer.py   # Per-field fill-or-escalate decision
│   │   ├── app_queue.py           # Priority queue + rate limiter
│   │   ├── state_machine.py       # Application lifecycle state transitions
│   │   └── email_parser.py        # Optional: parse stage updates from email
│   │
│   ├── automation/
│   │   ├── __init__.py
│   │   ├── anti_detect.py         # playwright-stealth config, timing engine
│   │   ├── base_handler.py        # Abstract base class all handlers inherit
│   │   ├── linkedin_handler.py    # LinkedIn Easy Apply automation
│   │   ├── indeed_handler.py      # Indeed Quick Apply automation
│   │   ├── greenhouse_handler.py  # Greenhouse + Lever ATS handler
│   │   ├── workday_handler.py     # Workday ATS handler (best-effort)
│   │   └── assisted_mode.py      # Visible browser fallback
│   │
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base_scraper.py        # Abstract scraper base
│   │   ├── linkedin_scraper.py
│   │   ├── indeed_scraper.py
│   │   └── company_site_scraper.py
│   │
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── applications.py        # CRUD + stage updates
│   │   ├── queue.py               # Queue management endpoints
│   │   ├── master_resume.py       # Resume CRUD
│   │   ├── answer_bank.py         # Answer bank CRUD
│   │   ├── analytics.py           # Aggregation queries
│   │   ├── settings.py            # Config read/write
│   │   └── pending.py             # Human review queue
│   │
│   └── utils/
│       ├── llm.py                 # Anthropic client wrapper, retry logic
│       ├── hashing.py             # Content hash helpers
│       └── logging.py             # Structured JSON logger
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── pages/
│   │   │   ├── Kanban.tsx         # Application pipeline board
│   │   │   ├── Pending.tsx        # Human review queue
│   │   │   ├── Analytics.tsx      # Funnel + metrics
│   │   │   ├── ResumeEditor.tsx   # Master resume JSON editor
│   │   │   └── Settings.tsx       # Config + answer bank
│   │   ├── components/
│   │   │   ├── AppCard.tsx
│   │   │   ├── DetailDrawer.tsx
│   │   │   ├── AnswerForm.tsx
│   │   │   └── StageTimeline.tsx
│   │   └── store/
│   │       └── useAppStore.ts
│   └── vite.config.ts
│
├── data/
│   ├── master_resume.json         # User's master resume (source of truth)
│   ├── config.yaml                # All user-configurable settings
│   ├── job_agent.db               # SQLite database
│   └── resumes/                   # Rendered PDF/DOCX outputs (gitignored)
│
├── templates/
│   └── resume.tex                 # LaTeX resume template
│
└── docker-compose.yml             # Optional: containerized run
```

---

## Database Schema

Create all tables via SQLAlchemy models in `models.py`. Use async SQLAlchemy with aiosqlite driver.

```sql
-- Applications: core record per job applied to or tracked
CREATE TABLE applications (
    id TEXT PRIMARY KEY,                    -- UUID4
    company TEXT NOT NULL,
    role_title TEXT NOT NULL,
    source_url TEXT,
    source_platform TEXT,                   -- linkedin | indeed | greenhouse | lever | workday | direct | manual
    jd_hash TEXT,                           -- FK to jd_cache
    jd_raw TEXT,                            -- archived JD text
    resume_version_id TEXT,                 -- FK to resume_versions
    status TEXT NOT NULL DEFAULT 'discovered',  -- see ApplicationStatus enum
    stage TEXT NOT NULL DEFAULT 'none',     -- see ApplicationStage enum
    match_score REAL,
    applied_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    is_assisted INTEGER DEFAULT 0,
    is_confirmed INTEGER DEFAULT 0,
    apply_mode TEXT                         -- auto | assisted | manual
);

-- Stage history: full audit trail of every stage transition
CREATE TABLE stage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    changed_by TEXT NOT NULL DEFAULT 'system',  -- system | human
    notes TEXT
);

-- JD cache: parsed job descriptions, shared across platforms
CREATE TABLE jd_cache (
    content_hash TEXT PRIMARY KEY,
    raw_text TEXT NOT NULL,
    parsed_json TEXT,                       -- JSON blob: ParsedJD schema
    parse_confidence REAL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Resume versions: every tailored resume linked to one application
CREATE TABLE resume_versions (
    id TEXT PRIMARY KEY,
    application_id TEXT,
    tailored_json TEXT NOT NULL,            -- JSON blob
    pdf_path TEXT,
    docx_path TEXT,
    render_hash TEXT,                       -- hash of tailored_json for cache lookup
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Answer bank: user's answers to common form fields
CREATE TABLE answer_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,              -- canonical key, e.g. "salary_expectation_sgd"
    value TEXT NOT NULL,
    format_hint TEXT,                      -- range | point | text | boolean | yesno
    country_tag TEXT,                      -- optional: SG | US | UK | null = all
    usage_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pending clarifications: form fields queued for human review
CREATE TABLE pending_clarifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_label TEXT,
    question_text TEXT,
    suggested_answer TEXT,
    confidence REAL,
    status TEXT DEFAULT 'pending',         -- pending | resolved | skipped
    resolved_answer TEXT,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seen hashes: deduplication store
CREATE TABLE seen_hashes (
    content_hash TEXT PRIMARY KEY,
    company TEXT,
    role_title TEXT,
    first_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### Enums (define in `models.py` as Python Enum classes)

```python
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
    NONE = "none"              # not yet applied
    APPLIED = "applied"
    SCREENING = "screening"
    OA = "oa"                  # online assessment
    PHONE = "phone"
    INTERVIEW_1 = "interview_1"
    INTERVIEW_2 = "interview_2"
    INTERVIEW_3 = "interview_3"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    WITHDRAWN = "withdrawn"
```

---

## Master Resume JSON Schema

This is the user's single source of truth, stored at `data/master_resume.json`. Define the Pydantic model in `schemas.py`.

```json
{
  "personal": {
    "name": "string",
    "email": "string",
    "phone": "string",
    "location": "string",
    "linkedin": "string",
    "github": "string",
    "website": "string"
  },
  "summary": "string | null",
  "work_experience": [
    {
      "role": "string",
      "company": "string",
      "location": "string",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM | null",
      "is_current": false,
      "bullets": [
        {
          "id": "uuid4 string",
          "text": "string — the original bullet text",
          "skills": ["Python", "FastAPI"],
          "domain": "finance | engineering | data | research | ops | other",
          "action_verb": "string",
          "has_metric": true,
          "impact_score": 0.8,
          "callback_count": 0,
          "application_count": 0
        }
      ]
    }
  ],
  "education": [
    {
      "degree": "string",
      "field": "string",
      "institution": "string",
      "location": "string",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM | null",
      "gpa": "string | null",
      "honors": "string | null",
      "relevant_coursework": ["string"]
    }
  ],
  "skills": {
    "languages": ["Python", "Java"],
    "frameworks": ["FastAPI", "React"],
    "tools": ["Docker", "Git"],
    "databases": ["PostgreSQL", "SQLite"],
    "domains": ["NLP", "Machine Learning"],
    "other": []
  },
  "projects": [
    {
      "name": "string",
      "description": "string",
      "tech_stack": ["string"],
      "bullets": [
        {
          "id": "uuid4 string",
          "text": "string",
          "skills": ["string"],
          "domain": "string",
          "has_metric": false,
          "impact_score": 0.5,
          "callback_count": 0,
          "application_count": 0
        }
      ],
      "url": "string | null"
    }
  ],
  "certifications": [
    {
      "name": "string",
      "issuer": "string",
      "date": "YYYY-MM",
      "url": "string | null"
    }
  ]
}
```

---

## Config File Schema

`data/config.yaml` — user-editable, loaded via Pydantic Settings on startup.

```yaml
# LLM
anthropic_api_key: "sk-ant-..."
llm_model: "claude-sonnet-4-20250514"

# Application limits
daily_cap: 15
per_platform_caps:
  linkedin: 10
  indeed: 8
  greenhouse: 5
  workday: 3

# Queue dispatch window (24h format, local time)
dispatch_window:
  days: ["tuesday", "wednesday", "thursday"]
  start_hour: 9
  end_hour: 11

# Match scoring thresholds
min_match_score: 65             # Below this: not queued
human_approval_above: 80        # Above this: require explicit human green-light before dispatch

# Confidence thresholds
autofill_confidence_threshold: 0.85

# Rate limiting delays (seconds)
action_delay_mean: 0.5
action_delay_stddev: 0.2

# Salary expectations (by currency)
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
require_sponsorship: false       # true if you need visa sponsorship
work_authorization:
  SG: true
  US: false
  UK: false

# Soft filters
min_role_level: "junior"         # junior | mid | senior
exclude_contract_only: false

# LaTeX template path
latex_template: "templates/resume.tex"

# Scrapers
scrapers:
  linkedin:
    enabled: true
    search_keywords: ["software engineer", "backend developer"]
    location: "Singapore"
    easy_apply_only: false
  indeed:
    enabled: true
    search_keywords: ["software engineer"]
    location: "Singapore, SG"
  company_sites: []              # list of {name, careers_url, css_selector_for_listings}
```

---

## Service Specifications

### `services/jd_parser.py`

**Purpose**: Convert raw JD text into a structured `ParsedJD` Pydantic object.

```python
class ParsedJD(BaseModel):
    required_skills: list[str]
    preferred_skills: list[str]
    responsibilities: list[str]
    tech_stack: list[str]
    role_level: str              # junior | mid | senior | lead | unknown
    domain: str                  # finance | trading | software | data | infra | other
    years_experience_min: int | None
    years_experience_max: int | None
    culture_signals: list[str]   # ["startup", "fast-paced", "remote-first"]
    red_flags: list[str]         # ["requires clearance", "relocation required"]
    sponsorship_available: bool | None
    remote_type: str             # remote | hybrid | onsite | unknown
    confidence: float            # 0–1 overall parse confidence
    raw_keywords: list[str]      # all significant terms extracted

async def parse_jd(raw_text: str, jd_hash: str) -> ParsedJD:
    """
    1. Check jd_cache table by jd_hash. If found and parsed_json is not null, return cached result.
    2. Call LLM with structured output prompt.
    3. Save result to jd_cache.
    4. Return ParsedJD.
    """
```

**LLM prompt template** (write this as a proper f-string in the service):

```
You are a precise job description parser. Extract structured data from the job description below.
Return ONLY valid JSON matching this exact schema. No markdown, no explanation.

Schema:
{
  "required_skills": ["list of explicitly required skills/technologies"],
  "preferred_skills": ["nice-to-have skills"],
  "responsibilities": ["key responsibilities, max 8, each under 15 words"],
  "tech_stack": ["all mentioned technologies, tools, languages"],
  "role_level": "junior|mid|senior|lead|unknown",
  "domain": "finance|trading|software|data|infra|other",
  "years_experience_min": null or integer,
  "years_experience_max": null or integer,
  "culture_signals": ["list of culture/environment descriptors"],
  "red_flags": ["list of potential constraints like visa restrictions, relocation, clearance"],
  "sponsorship_available": null or boolean,
  "remote_type": "remote|hybrid|onsite|unknown",
  "confidence": 0.0 to 1.0,
  "raw_keywords": ["all significant domain/skill terms found"]
}

Job Description:
{raw_text}
```

---

### `services/match_scorer.py`

**Purpose**: Score 0–100 how well the master resume matches a parsed JD.

```python
class MatchResult(BaseModel):
    overall_score: float         # 0–100
    required_coverage: float     # % of required skills with evidence
    experience_relevance: float  # semantic match of experience to responsibilities
    domain_alignment: float      # finance/trading/software domain match
    seniority_fit: float         # experience level vs role expectation
    missing_required: list[str]  # required skills with zero evidence
    partial_required: list[str]  # required skills with weak evidence
    strong_matches: list[str]    # required skills with strong evidence

async def score_match(master_resume: dict, parsed_jd: ParsedJD) -> MatchResult:
    """
    1. Fast pre-filter: compute keyword overlap ratio. If < 0.25, return score=0 without LLM call.
    2. Extract all skills/keywords from master_resume (flattened from bullets + skills section).
    3. Compute required_coverage: exact + fuzzy match of parsed_jd.required_skills against resume skills.
    4. Call LLM for experience_relevance and domain_alignment (these require semantic understanding).
    5. Compute seniority_fit from years_experience_min vs inferred user YOE.
    6. Compute weighted final score:
       required_coverage * 0.40 + experience_relevance * 0.30 + domain_alignment * 0.20 + seniority_fit * 0.10
    7. Normalize to 0–100 range.
    """
```

---

### `services/bullet_selector.py`

**Purpose**: Select and reorder resume bullets for a specific application.

```python
class SelectedResume(BaseModel):
    """Same structure as master resume but with filtered/reordered bullets."""
    personal: dict
    summary: str | None
    work_experience: list[dict]  # each role has bullets: list of selected bullet dicts
    education: list[dict]
    skills: dict                 # reordered: JD-relevant skills first in each category
    projects: list[dict]         # reordered by relevance
    certifications: list[dict]
    section_order: list[str]     # e.g. ["work_experience", "projects", "skills", "education"]

async def select_bullets(
    master_resume: dict,
    parsed_jd: ParsedJD,
    match_result: MatchResult,
    max_bullets_per_role: int = 4
) -> SelectedResume:
    """
    For each work experience role and each project:
    1. Score every bullet against parsed_jd using LLM (batch all bullets for one role in a single call).
    2. Select top max_bullets_per_role bullets, ensuring collective coverage of JD requirements.
    3. Reorder selected bullets: highest relevance first.
    4. Reorder work experience roles: most recent first (always), but boost relevance signal.
    5. Reorder projects: most JD-relevant first.
    6. Reorder skills dict: within each category, JD-relevant skills first.
    7. Determine section_order based on role domain (tech role → projects before education).
    Return SelectedResume.
    """
```

---

### `services/keyword_injector.py`

**Purpose**: Rewrite selected bullets to naturally include missing JD keywords. Zero fabrication.

```python
class InjectionResult(BaseModel):
    bullet_id: str
    original_text: str
    rewritten_text: str | None   # None if injection was skipped
    injected_keywords: list[str]
    was_modified: bool
    skip_reason: str | None

async def inject_keywords(
    selected_resume: SelectedResume,
    parsed_jd: ParsedJD,
    match_result: MatchResult
) -> SelectedResume:
    """
    1. Identify missing required keywords: in parsed_jd.required_skills but not in selected bullets text.
    2. For each missing keyword:
       a. Find the bullet whose underlying fact most plausibly supports this keyword.
       b. If plausibility score < 0.7: skip, mark as gap. Do NOT inject.
       c. If plausibility >= 0.7: rewrite bullet with LLM.
    3. Limit: max 2 rewrites per work experience role, max 1 per project.
    4. Post-injection verification: call LLM again to confirm no fabrication occurred.
       If verification fails: revert to original, log.
    5. Return updated SelectedResume with rewritten bullets.
    """
```

**Injection LLM prompt**:

```
You are rewriting a resume bullet point to naturally include a specific keyword.

STRICT RULES:
- The rewritten bullet must not introduce any new claim not present in the original.
- Preserve all numbers, percentages, and quantitative metrics exactly.
- Keep the same action verb or use a direct synonym.
- The rewrite must read naturally, not forced.
- If you cannot include the keyword without fabricating, output SKIP.

Original bullet: {original_text}
Keyword to include: {keyword}
Context (why this keyword fits): {context}

Output ONLY the rewritten bullet text, or the single word SKIP.
```

---

### `services/answer_bank.py`

**Purpose**: Store and retrieve answers to application form fields.

```python
class AnswerLookupResult(BaseModel):
    key: str
    value: str
    confidence: float            # 1.0 exact, 0.7-0.99 fuzzy, 0.5-0.69 inferred
    match_type: str              # exact | fuzzy | inferred | not_found
    format_hint: str | None

async def lookup_answer(
    field_label: str,
    field_type: str,             # text | dropdown | checkbox | yesno | number
    context: dict | None = None  # application context (company, country, etc.)
) -> AnswerLookupResult:
    """
    1. Normalize field_label: lowercase, strip punctuation.
    2. Exact match against answer_bank table key.
    3. If no exact match: fuzzy match using rapidfuzz.token_sort_ratio(). Threshold 0.75.
    4. If no fuzzy match: attempt inference (years of experience → compute from resume dates).
    5. Apply country_tag filter if context includes country.
    6. Format value for field_type (e.g. salary as "SGD 70,000 - 95,000" for text, "70000" for number).
    7. Increment usage_count on match.
    """

# Always-escalate field patterns (case-insensitive regex)
ALWAYS_ESCALATE_PATTERNS = [
    r"cover.?letter",
    r"why.+(want|join|interested|excited).+(company|role|position|us)",
    r"tell.+about.+yourself",
    r"motivat",
    r"what.+bring.+to",
    r"personal.+statement",
]
```

**Pre-seeded answer bank keys** (create a `seed_answer_bank()` function called on first run):

```
salary_expectation_sgd, salary_expectation_usd, graduation_date,
work_authorization_sg, work_authorization_us, work_authorization_uk,
notice_period_weeks, years_experience_python, years_experience_java,
linkedin_url, github_url, portfolio_url, phone_number, current_location,
willing_to_relocate, preferred_work_type (remote/hybrid/onsite),
highest_education_level, university_name, degree_name, gpa,
employment_status (employed/unemployed/student)
```

---

### `services/confidence_scorer.py`

**Purpose**: Decide whether to auto-fill or escalate each form field.

```python
class FieldDecision(BaseModel):
    field_name: str
    field_label: str
    decision: str                # autofill | escalate | skip (optional empty field)
    answer: str | None
    confidence: float
    reason: str

async def decide_field(
    field_label: str,
    field_type: str,
    is_required: bool,
    context: dict
) -> FieldDecision:
    """
    1. Check always-escalate patterns first. If matches: return escalate regardless.
    2. Look up answer_bank.
    3. If match confidence >= config.autofill_confidence_threshold: autofill.
    4. If below threshold and is_required: escalate.
    5. If below threshold and not required: skip (leave blank).
    6. Log decision to audit table.
    """
```

---

### `automation/base_handler.py`

**Purpose**: Abstract base all platform handlers inherit from.

```python
class ApplicationResult(BaseModel):
    success: bool
    application_id: str
    confirmation_text: str | None
    screenshot_path: str | None
    fields_filled: list[str]
    fields_escalated: list[str]
    error: str | None
    fallback_to_assisted: bool = False

class BaseHandler(ABC):
    def __init__(self, page: Page, config: Config, answer_bank: AnswerBankService):
        self.page = page
        self.config = config
        self.answer_bank = answer_bank
        self.anti_detect = AntiDetect(config)

    @abstractmethod
    async def apply(self, url: str, application: Application, resume_path: str) -> ApplicationResult:
        """Entry point for each platform handler."""

    async def fill_field(self, selector: str, value: str) -> None:
        """Fill a form field with human-like timing."""
        await self.anti_detect.random_delay()
        await self.page.click(selector)
        await self.anti_detect.random_delay(mean=0.2)
        await self.page.fill(selector, value)

    async def upload_file(self, selector: str, file_path: str) -> None:
        """Handle file input upload."""

    async def take_screenshot(self, label: str) -> str:
        """Save screenshot to data/screenshots/{application_id}_{label}.png, return path."""

    async def detect_confirmation(self) -> str | None:
        """Check for common success confirmation messages. Return text or None."""

    async def detect_error(self) -> str | None:
        """Check for validation errors or failure states. Return message or None."""
```

---

### `automation/anti_detect.py`

```python
class AntiDetect:
    def __init__(self, config: Config):
        self.mean = config.action_delay_mean
        self.stddev = config.action_delay_stddev

    async def random_delay(self, mean: float | None = None, stddev: float | None = None):
        """Sleep for gaussian-distributed duration. Clamp to [0.1, 5.0] seconds."""
        delay = max(0.1, min(5.0, random.gauss(mean or self.mean, stddev or self.stddev)))
        await asyncio.sleep(delay)

    async def move_mouse_human(self, page: Page, x: int, y: int):
        """Move mouse along a curved path (bezier) to (x, y)."""

    @staticmethod
    def get_stealth_launch_args() -> dict:
        """Return Playwright launch args + context args for playwright-stealth."""
        return {
            "launch_args": ["--disable-blink-features=AutomationControlled"],
            "context_args": {
                "viewport": {"width": 1440, "height": 900},
                "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
                "locale": "en-SG",
                "timezone_id": "Asia/Singapore",
            }
        }
```

---

### `automation/linkedin_handler.py`

Key logic to implement:

1. Navigate to job URL, wait for Easy Apply button (selector: `button[aria-label*="Easy Apply"]`).
2. Click Easy Apply → modal opens with multi-step form.
3. Loop: detect current step's form fields, fill each via `decide_field()`.
4. On resume upload step: upload tailored PDF.
5. If a field triggers escalation: pause, add to pending_clarifications, abort this application (do not submit partial), set status to `pending_clarification`.
6. Click Next/Continue until Submit button is visible.
7. Click Submit, detect confirmation.
8. Handle redirect to external site: detect URL change away from linkedin.com → log redirect target, attempt handoff to appropriate handler or fall back to assisted mode.

---

### `automation/workday_handler.py`

This is best-effort. Implement with these fallback tiers:

1. **Tier 1**: Try version-specific Playwright selectors (implement Workday v2023 layout).
2. **Tier 2**: Screenshot current page → send to LLM with prompt "Identify all form field labels and their CSS selectors" → fill using returned selectors.
3. **Tier 3**: Fall back to `assisted_mode.py`.

Never raise an exception from Workday handler — always degrade gracefully to the next tier.

---

### `automation/assisted_mode.py`

```python
async def run_assisted(
    url: str,
    application: Application,
    tailored_resume: SelectedResume,
    resume_pdf_path: str,
    pending_answers: dict[str, str]
) -> ApplicationResult:
    """
    1. Launch Playwright in NON-headless mode.
    2. Navigate to application URL.
    3. Print to console (and POST to /api/pending): all expected field answers.
    4. Copy tailored resume text to system clipboard.
    5. Wait for user to POST /api/applications/{id}/mark-applied or timeout after 30 minutes.
    6. On mark-applied: update application status, log as assisted.
    """
```

---

### `services/app_queue.py`

```python
class AppQueue:
    """
    Priority queue backed by SQLite (applications table with status=queued).
    Priority = match_score DESC, created_at ASC.
    """

    async def enqueue(self, application_id: str) -> None:
        """Set status=queued. Check dedup. Check daily cap before accepting."""

    async def dequeue_next(self) -> Application | None:
        """
        1. Check if current time is within dispatch_window. If not: return None.
        2. Check daily dispatched count < config.daily_cap.
        3. Check per-platform count < config.per_platform_caps[platform].
        4. Check: is there already an active application to same company today? If so: skip.
        5. Return highest-score queued application.
        """

    async def expire_stale(self, max_age_days: int = 14) -> int:
        """Mark applications queued > max_age_days as expired. Return count expired."""

    async def get_queue_stats(self) -> dict:
        """Return: total queued, by platform, estimated time to clear at current rate."""
```

---

### `services/state_machine.py`

```python
# Valid transitions map
VALID_TRANSITIONS: dict[ApplicationStatus, list[ApplicationStatus]] = {
    ApplicationStatus.DISCOVERED: [QUEUED, SKIPPED, EXPIRED],
    ApplicationStatus.QUEUED: [TAILORING, SKIPPED, EXPIRED],
    ApplicationStatus.TAILORING: [PENDING_CLARIFICATION, APPLYING, FAILED],
    ApplicationStatus.PENDING_CLARIFICATION: [APPLYING, SKIPPED],
    ApplicationStatus.APPLYING: [APPLIED, FAILED],
    ApplicationStatus.APPLIED: [APPLIED],     # stage changes happen independently
    ApplicationStatus.FAILED: [QUEUED],       # allow retry
}

async def transition(
    application_id: str,
    new_status: ApplicationStatus,
    new_stage: ApplicationStage | None = None,
    changed_by: str = "system",
    notes: str | None = None
) -> Application:
    """
    1. Validate transition is allowed per VALID_TRANSITIONS map.
    2. Update application status (and stage if provided).
    3. Write to stage_history table.
    4. Return updated Application.
    Raises ValueError if transition is not valid.
    """
```

---

## API Routes

All routes are prefixed with `/api`. Use FastAPI dependency injection for DB sessions.

### Applications

```
GET    /api/applications                    # list all, with filters: status, stage, platform, score_min
GET    /api/applications/{id}              # full detail including JD, resume version, stage history
PATCH  /api/applications/{id}/stage       # body: {stage, notes} — manual stage update
PATCH  /api/applications/{id}/mark-applied # for assisted mode completion
DELETE /api/applications/{id}             # soft delete (set status=skipped)
POST   /api/applications/manual           # manually add a job to track (body: {url, company, role, notes})
```

### Queue

```
GET    /api/queue                          # current queue with stats
POST   /api/queue/pause                   # pause all dispatch
POST   /api/queue/resume
POST   /api/queue/drain                   # clear entire queue
POST   /api/queue/force-add              # body: {url} — force a URL into the queue
GET    /api/queue/stats                   # daily counts, cap status, estimated clear time
```

### Pending Review

```
GET    /api/pending                        # all pending_clarifications with application context
POST   /api/pending/{id}/resolve          # body: {answer, save_to_bank: bool}
POST   /api/pending/{id}/skip             # skip this field (application will be skipped if required)
POST   /api/pending/{id}/reject-app       # reject entire application, not just the field
```

### Answer Bank

```
GET    /api/answer-bank                   # all answers
POST   /api/answer-bank                   # add answer
PUT    /api/answer-bank/{id}              # update answer
DELETE /api/answer-bank/{id}             # delete answer
POST   /api/answer-bank/test-lookup       # body: {field_label} — test what would be returned
```

### Analytics

```
GET    /api/analytics/funnel              # applications by stage counts
GET    /api/analytics/response-rate       # response rate by platform, score tier, role type
GET    /api/analytics/keyword-effectiveness  # which JD keywords correlate with callbacks
GET    /api/analytics/velocity            # apps/week over time
GET    /api/analytics/bullets             # callback rate per bullet_id
```

### Master Resume

```
GET    /api/resume                        # current master resume JSON
PUT    /api/resume                        # full replace (validated against schema)
POST   /api/resume/validate               # validate JSON without saving
POST   /api/resume/preview-tailor         # body: {jd_text} — preview tailored output without creating application
```

### Settings

```
GET    /api/settings                      # current config.yaml as JSON
PUT    /api/settings                      # write config.yaml (validated)
GET    /api/settings/platforms            # session status per platform (authenticated/expired)
POST   /api/settings/platforms/{name}/refresh-session  # trigger re-auth
```

---

## Frontend Pages

### `Kanban.tsx`
- Horizontal column layout. Columns: Applied, Screening, OA, Phone, Interview, Offer, Rejected, Ghosted.
- Each card: company name + logo (favicon), role title, applied date, match score badge (green ≥ 75 / amber 55–74 / red < 55), days in current stage.
- Drag card to new column → PATCH `/api/applications/{id}/stage`.
- Click card → opens `DetailDrawer` with full application details.
- Header bar: queue status badge (N queued), pause/resume toggle, pending count badge.

### `Pending.tsx`
- List of all pending clarifications sorted by application match score descending.
- Each item: company + role header, field label, full question text, pre-filled suggested answer (editable), confidence score.
- Actions: Confirm (saves answer + optionally to bank), Skip, Reject Application.
- Keyboard shortcuts: Tab to next, Enter to confirm, Escape to skip.
- Progress bar: X of Y cleared.

### `Analytics.tsx`
- Funnel chart: horizontal bar per stage, showing drop-off at each step.
- Response rate by platform: bar chart.
- Application velocity: line chart, apps/week over rolling 12 weeks.
- Score distribution: histogram of match scores at time of application.
- Keyword effectiveness: table of top 20 JD keywords with callback rate when present.

### `ResumeEditor.tsx`
- Monaco editor (JSON mode) showing master resume.
- Live validation against Pydantic schema via POST `/api/resume/validate`.
- Side panel: preview of rendered sections.
- "Preview Tailored" button: paste a JD and see how the system would tailor it, without creating an application.
- Save → PUT `/api/resume`.

### `Settings.tsx`
- Form-based config editor (no raw YAML).
- Platform sessions section: shows auth status per platform, re-auth button.
- Answer bank section: searchable table of all stored answers, inline edit/delete, add new.
- Danger zone: drain queue, clear application history.

---

## Background Jobs (APScheduler)

Register all jobs in `main.py` using `AsyncIOScheduler`. All jobs are async functions.

```python
# Runs every 6 hours
scheduler.add_job(run_discovery_cycle, 'interval', hours=6)

# Runs every 5 minutes during dispatch window
scheduler.add_job(dispatch_next_application, 'interval', minutes=5)

# Runs every 30 minutes
scheduler.add_job(check_pending_clarifications, 'interval', minutes=30)

# Runs daily at 2am
scheduler.add_job(expire_stale_queue_items, 'cron', hour=2)
```

`run_discovery_cycle()` orchestration:
1. Run all enabled scrapers concurrently (`asyncio.gather`).
2. For each discovered job: dedup check → red flag filter → parse JD → score match → if score >= min_match_score: enqueue.

`dispatch_next_application()`:
1. Call `app_queue.dequeue_next()`. If None: return early.
2. Run tailoring pipeline: `select_bullets` → `inject_keywords` → `render_resume`.
3. Run clarification check across all known form fields for this platform.
4. If any required field has pending clarification: set status=pending_clarification, notify.
5. Otherwise: launch automation handler, run apply, update status.

---

## Resume Rendering (`services/resume_renderer.py`)

```python
async def render_resume(
    selected_resume: SelectedResume,
    application_id: str,
    format: str = "pdf"  # pdf | docx
) -> str:
    """
    1. Check render cache: compute hash of selected_resume JSON. 
       If hash exists in resume_versions table with pdf_path: return cached path.
    2. Render LaTeX from template using Jinja2 (load templates/resume.tex as Jinja2 template).
    3. Write .tex file to data/resumes/{application_id}.tex.
    4. Run: subprocess.run(["pdflatex", "-interaction=nonstopmode", ...]).
    5. If pdflatex fails: fall back to python-docx render.
    6. Save path to resume_versions table.
    7. Return absolute path to PDF/DOCX.
    """
```

The LaTeX template (`templates/resume.tex`) must:
- Use Jinja2 delimiters `\VAR{...}` and `\BLOCK{...}` (to avoid conflict with LaTeX `{}`).
- Support dynamic section ordering via `selected_resume.section_order`.
- Have a clean single-page design with configurable margins (default: 0.5in all sides).
- Auto-escape all LaTeX special characters in user-provided text.

---

## LLM Client (`utils/llm.py`)

```python
async def call_llm(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 1500,
    expect_json: bool = False,
    retry_count: int = 3
) -> str:
    """
    Wrapper around Anthropic async client.
    - Retry on rate limit (429) with exponential backoff.
    - If expect_json=True: strip markdown fences, validate JSON parseable, retry if not.
    - Log every call: timestamp, prompt hash, token usage, latency.
    - Never log raw prompt text containing user PII to disk.
    """
```

---

## Build Phases

Implement in this exact order. Do not skip phases or build ahead.

### Phase 1 — Data Foundation (start here)

1. Set up directory structure exactly as specified.
2. Implement `database.py`: async SQLAlchemy engine, `get_session` dependency, `init_db()` function that creates all tables.
3. Implement `models.py`: all SQLAlchemy ORM models matching the schema above.
4. Implement `schemas.py`: all Pydantic models including `MasterResume`, `ParsedJD`, `MatchResult`, `SelectedResume`.
5. Implement `config.py`: Pydantic Settings loading `data/config.yaml`.
6. Create a sample `data/master_resume.json` with realistic placeholder data matching the schema exactly.
7. Create `data/config.yaml` with all fields populated with sensible defaults.
8. Write a `pytest` test: load master_resume.json, validate against MasterResume Pydantic model, assert no validation errors.

**Deliverable**: `python -c "from backend.database import init_db; import asyncio; asyncio.run(init_db())"` creates all DB tables without error. Master resume loads and validates.

### Phase 2 — LLM Pipeline

1. Implement `utils/llm.py` with retry and JSON validation.
2. Implement `services/jd_parser.py` with caching.
3. Implement `services/match_scorer.py` with keyword pre-filter.
4. Implement `services/gap_analyzer.py`.
5. Implement `services/bullet_selector.py`.
6. Implement `services/keyword_injector.py` with post-injection verification.
7. Implement `services/resume_renderer.py` with pdflatex + python-docx fallback.
8. Write a CLI test script `scripts/test_pipeline.py` that: takes a JD text as input → runs full pipeline → outputs tailored resume JSON + PDF path.

**Deliverable**: Running `python scripts/test_pipeline.py "JD text here"` produces a tailored PDF in `data/resumes/`.

### Phase 3 — Answer Bank + Clarification

1. Implement `services/answer_bank.py` with fuzzy matching and always-escalate patterns.
2. Implement `services/confidence_scorer.py`.
3. Implement `seed_answer_bank()` function.
4. Implement `routers/answer_bank.py` and `routers/pending.py`.
5. Add these routes to `main.py`.

**Deliverable**: Answer bank CRUD via API. `POST /api/answer-bank/test-lookup` with field labels returns correct answers/confidence.

### Phase 4 — Queue + State Machine

1. Implement `services/state_machine.py`.
2. Implement `services/app_queue.py`.
3. Implement `routers/applications.py` and `routers/queue.py`.
4. Implement `services/deduplicator.py`.
5. Manually add a test application via `POST /api/applications/manual` and verify state transitions work via `PATCH /api/applications/{id}/stage`.

**Deliverable**: Full application CRUD. State machine rejects invalid transitions with clear error.

### Phase 5 — Browser Automation

1. Install Playwright: `playwright install chromium`.
2. Install playwright-stealth.
3. Implement `automation/anti_detect.py`.
4. Implement `automation/base_handler.py`.
5. Implement `automation/assisted_mode.py` (test this first — safest).
6. Implement `automation/linkedin_handler.py`.
7. Implement `automation/indeed_handler.py`.
8. Implement `automation/greenhouse_handler.py`.
9. Implement `automation/workday_handler.py` (all three tiers).

Test each handler in dry-run mode (navigate to form, detect fields, do NOT submit) before enabling real submission.

**Deliverable**: LinkedIn handler correctly navigates Easy Apply, detects form fields, and falls back to assisted mode on any error.

### Phase 6 — Scrapers

1. Implement `scrapers/base_scraper.py`.
2. Implement `scrapers/linkedin_scraper.py` using authenticated session.
3. Implement `scrapers/indeed_scraper.py`.
4. Implement `services/discovery.py` orchestration.
5. Implement `services/red_flag_detector.py`.
6. Wire up background scheduler in `main.py`.

**Deliverable**: `POST /api/queue/force-add` with a real LinkedIn Easy Apply URL correctly parses the JD, scores the match, and adds to queue.

### Phase 7 — Dashboard Frontend

1. Scaffold Vite + React + TypeScript + TailwindCSS frontend.
2. Install: `@tanstack/react-query`, `zustand`, `@monaco-editor/react`, `recharts`, `react-beautiful-dnd`.
3. Build pages in this order: Settings → Pending → Kanban → Analytics → ResumeEditor.
4. Configure Vite proxy: `/api` → `http://localhost:8000`.

**Deliverable**: Full dashboard accessible at `http://localhost:5173`. Kanban shows applications. Pending queue shows any pending clarifications. Settings loads and saves config.

### Phase 8 — Integration + Hardening

1. End-to-end test: force-add a real LinkedIn URL → run full pipeline → verify tailored PDF generated → verify application in Kanban.
2. Add rate limit enforcement to all scrapers and handlers.
3. Add session warmup to anti-detect module.
4. Implement daily dispatch scheduler.
5. Add `POST /api/analytics/bullets/update-callback` endpoint (called when you mark a stage update to screening or beyond — updates bullet callback_count).
6. Write a `scripts/setup.py` that: checks all dependencies, initializes DB, seeds answer bank with prompts to fill in your answers.

---

## Important Implementation Notes

**On async**: Use `async`/`await` throughout. The Playwright API is async. SQLAlchemy async sessions must be properly managed with context managers. Do not use sync Playwright or sync SQLAlchemy.

**On errors**: Every handler must catch all exceptions, log them fully (application_id, error type, stack trace), set application status to `failed`, and never surface raw exceptions to the API layer (return proper HTTP error responses).

**On logging**: Use Python's `logging` module with a JSON formatter. Log level INFO for normal operations, DEBUG for LLM calls and form fills. Every log line must include `application_id` where relevant.

**On the no-fabrication constraint**: The `keyword_injector.py` system prompt must include this instruction on every call: `"CRITICAL: You may only describe what actually happened. Never introduce a skill, technology, outcome, or experience that is not already described in the original bullet. When in doubt, output SKIP."` Add a post-injection verification call that explicitly checks for fabrication.

**On testing**: Write pytest tests for `state_machine.py` (test every valid and invalid transition), `answer_bank.py` (test fuzzy matching accuracy), and `match_scorer.py` (test that a resume matching all required skills scores ≥ 80).

**On secrets**: The `anthropic_api_key` and any platform credentials must never be committed to git. Add `data/`, `*.db`, `data/resumes/`, and any credential files to `.gitignore` immediately.

---

## First Command to Run

```bash
cd job-agent
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install fastapi uvicorn[standard] sqlalchemy[asyncio] aiosqlite pydantic pydantic-settings \
    anthropic playwright python-docx jinja2 rapidfuzz apscheduler httpx aiofiles \
    pytest pytest-asyncio
playwright install chromium
pip install playwright-stealth
python scripts/setup.py
uvicorn backend.main:app --reload --port 8000
```

Start with Phase 1. Confirm each phase deliverable before moving to the next.

---

## Project Name

**Relay** — your always-on job application relay station. It relays your resume to employers, runs as a relay station (perpetual, autonomous), and executes like a relay race (each component passing to the next without dropping the baton).

All user-facing strings, dashboard title, and README should use this name.

---

## Deployment Target: Oracle Cloud Always Free (ARM)

The system must run **perpetually at zero cost**. The deployment target is **Oracle Cloud Infrastructure Always Free Tier** using an **Ampere A1 ARM instance**.

Why Oracle ARM Free Tier over all other options:
- 4 ARM cores + **24GB RAM** — genuinely always free, no expiry, no sleep/spindown
- 50GB block storage for SQLite + rendered PDFs
- 10TB outbound data/month
- Runs headless Chromium without memory pressure
- Supports Docker, systemd, everything you need

**No other free tier gives you 24GB RAM with no time limit.** Fly.io free (256MB), Render free (spins down), Railway free (500h/month) are all inadequate for persistent Playwright.

Signup: cloud.oracle.com → Create Account → Choose Always Free resources → Ampere A1 (4 OCPU, 24GB RAM as a single VM).

---

## Architecture Changes for Perpetual Free Deployment

### 1. Replace pdflatex with WeasyPrint

pdflatex requires a full LaTeX installation (~600MB). On a free VM, this is wasteful. Replace entirely.

```
pip install weasyprint
```

`resume_renderer.py` renders an **HTML template → PDF via WeasyPrint**. Replace `templates/resume.tex` with `templates/resume.html` (Jinja2 HTML template styled with inline CSS for PDF rendering).

WeasyPrint produces print-quality PDFs from HTML/CSS. It handles fonts, margins, page breaks. A clean resume CSS template is sufficient. python-docx remains as the DOCX fallback.

Update `requirements.txt`: remove `pdflatex` dependency note, add `weasyprint`.

### 2. Single-Process Deployment (FastAPI serves frontend)

Do not run a separate Vite dev server in production. Build React to static files and serve from FastAPI.

```python
# In main.py, after all API routers are registered:
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")
```

Add to `package.json` scripts:
```json
"build:prod": "vite build --outDir ../frontend/dist"
```

Deployment starts one process: `uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1`

### 3. Playwright Memory Optimization

On the ARM VM, memory is not the constraint (24GB), but on any future lower-spec environment, always launch Playwright with these flags:

```python
# In anti_detect.py, get_stealth_launch_args():
launch_args = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--headless=new",              # newer headless mode, more stable
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--mute-audio",
]
```

Always close the browser context immediately after each application — never leave it open idle:

```python
async with async_playwright() as p:
    browser = await p.chromium.launch(**launch_args)
    context = await browser.new_context(**context_args)
    try:
        result = await handler.apply(...)
    finally:
        await context.close()
        await browser.close()
```

### 4. Secrets via Environment Variables (Not Config File)

On the server, secrets must be environment variables, not a YAML file. Update `config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",           # for local development
        env_file_encoding="utf-8",
        extra="allow"
    )
    
    anthropic_api_key: str
    secret_key: str = "change-me-in-production"
    # All other settings can still come from config.yaml
    # Only secrets come from env vars
```

On the Oracle VM, set via:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export SECRET_KEY="your-random-secret"
```

Or add to `/etc/environment` for persistence across reboots.

### 5. SQLite WAL Mode

Enable Write-Ahead Logging for better concurrent performance. Add to `database.py`:

```python
from sqlalchemy import event

@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
    cursor.close()
```

### 6. systemd Service File

Create `deploy/relay.service`. This keeps Relay running perpetually, auto-restarts on crash, and starts on VM reboot:

```ini
[Unit]
Description=Relay Job Application Agent
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/relay
Environment=ANTHROPIC_API_KEY=sk-ant-your-key-here
Environment=SECRET_KEY=your-secret-here
ExecStart=/home/ubuntu/relay/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Install and enable:
```bash
sudo cp deploy/relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable relay
sudo systemctl start relay
# Check status:
sudo systemctl status relay
# Tail logs:
sudo journalctl -u relay -f
```

### 7. Split Automation Architecture (Recommended)

Browser automation (Playwright) submitting actual applications is best run with a **residential IP**, not a cloud IP. Cloud VM IPs are datacenter ranges — LinkedIn and Indeed detect and throttle them aggressively.

**Recommended split**:

| Component | Runs on | Why |
|---|---|---|
| FastAPI backend | Oracle Cloud VM | Always-on, free |
| SQLite database | Oracle Cloud VM | Persistent storage |
| APScheduler (discovery, parsing, scoring) | Oracle Cloud VM | Continuous scraping fine from cloud IP |
| React dashboard | Served from Oracle Cloud VM | Accessible from anywhere |
| Playwright automation (form submission only) | Your local machine | Residential IP = better stealth |

Implementation: Add a `POST /api/queue/claim-and-execute` endpoint the local automation agent calls periodically (every 5 minutes when your machine is on). It claims the next queued application, runs the browser handler locally, and reports back.

```python
# New router: routers/automation_agent.py
# POST /api/automation/claim     → returns next application ready to submit
# POST /api/automation/complete  → reports success/failure + confirmation text
# POST /api/automation/escalate  → reports a field needing human input
```

The local automation agent is a simple script (`scripts/local_agent.py`):

```python
# Runs on your local machine
# Polls /api/automation/claim every 5 minutes
# Executes the Playwright handler
# Reports result back to /api/automation/complete
# Optionally: only runs during configured hours

async def run_local_agent():
    while True:
        application = await claim_next()
        if application:
            result = await run_handler(application)
            await report_result(application.id, result)
        await asyncio.sleep(300)  # 5 minutes
```

This means the cloud VM handles everything **except** clicking Submit — the most detection-sensitive action. The cloud does all the heavy lifting (scraping, parsing, tailoring, queuing, tracking). Your laptop does the final mile.

If you want cloud-only (simpler), it still works — just accept that high daily volumes from a cloud IP will eventually get flagged. At ≤ 10 applications/day with proper delays, it's generally fine.

### 8. Nginx Reverse Proxy + HTTPS

On the Oracle VM, put Nginx in front of uvicorn. Add a free SSL cert via Let's Encrypt. This lets you access your dashboard from anywhere via `https://yourdomain.com`.

```bash
sudo apt install nginx certbot python3-certbot-nginx
```

`deploy/nginx.conf`:
```nginx
server {
    listen 80;
    server_name yourdomain.com;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

```bash
sudo certbot --nginx -d yourdomain.com
# Auto-renews via cron. HTTPS forever, free.
```

Use a free subdomain from **duckdns.org** or **afraid.org** if you don't want to buy a domain.

### 9. Dashboard Authentication

The dashboard is exposed to the internet. Add simple token-based auth. A single hardcoded token in config is sufficient for personal use.

```python
# In main.py, add a middleware or dependency:
from fastapi import Security, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != settings.secret_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return credentials.credentials
```

Apply `Depends(verify_token)` to all routers. The React frontend stores the token in memory (not localStorage) and includes it as `Authorization: Bearer <token>` on every request.

### 10. Deployment Script

Create `deploy/setup.sh` — runs once on a fresh Oracle VM to set everything up:

```bash
#!/bin/bash
# Run on fresh Ubuntu 22.04 Oracle ARM VM

# System deps
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip nginx git \
    libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 \  # WeasyPrint deps
    fonts-liberation fonts-dejavu

# Node (for frontend build)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Clone repo
git clone https://github.com/yourusername/relay.git /home/ubuntu/relay
cd /home/ubuntu/relay

# Python setup
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium

# Frontend build
cd frontend && npm install && npm run build:prod && cd ..

# Initialize DB + seed answer bank
python scripts/setup.py

# Install and start service
sudo cp deploy/relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable relay
sudo systemctl start relay

# Nginx setup
sudo cp deploy/nginx.conf /etc/nginx/sites-available/relay
sudo ln -s /etc/nginx/sites-available/relay /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx

echo "Relay is running. Access at http://$(curl -s ifconfig.me)"
```

---

## Updated Requirements File

```
# requirements.txt
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
sqlalchemy[asyncio]>=2.0.0
aiosqlite>=0.20.0
pydantic>=2.7.0
pydantic-settings>=2.2.0
anthropic>=0.28.0
playwright>=1.44.0
playwright-stealth>=1.0.6
weasyprint>=62.0          # replaces pdflatex
python-docx>=1.1.0
jinja2>=3.1.0
rapidfuzz>=3.9.0
apscheduler>=3.10.0
httpx>=0.27.0
aiofiles>=23.2.0
python-dotenv>=1.0.0
pytest>=8.2.0
pytest-asyncio>=0.23.0
```

---

## Cost Summary

| Component | Provider | Monthly Cost |
|---|---|---|
| VM (4 ARM cores, 24GB RAM) | Oracle Cloud Always Free | **$0** |
| Storage (50GB SSD) | Oracle Cloud Always Free | **$0** |
| SSL certificate | Let's Encrypt | **$0** |
| Domain (optional) | DuckDNS / afraid.org | **$0** |
| LLM API (Claude) | Anthropic | ~$2–5/month at 15 apps/day |
| **Total** | | **~$2–5/month** (API calls only) |

The only non-zero cost is LLM API usage. At 15 applications/day: ~1 parse call + ~1 scoring call + ~1 tailoring call per application = ~45 API calls/day × ~1500 tokens average = ~67,500 tokens/day. At Claude Sonnet pricing, this is approximately $2–5/month depending on match and how many bullets need rewriting.

