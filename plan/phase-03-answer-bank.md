# Phase 3 — Answer Bank + Clarification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Answer bank with fuzzy lookup + always-escalate patterns, confidence scorer, full CRUD API routes for answer bank and pending clarifications, and a minimal FastAPI `main.py` to wire everything together.

**Architecture:** `answer_bank.py` is a stateless async service wrapping DB queries + rapidfuzz. `confidence_scorer.py` calls it and returns a `FieldDecision`. Both routers use FastAPI `Depends(get_session)`. `main.py` registers all routers and runs `init_db()` on startup.

**Tech Stack:** FastAPI, SQLAlchemy async, rapidfuzz, pytest + AsyncMock

**Deliverable:** `uvicorn backend.main:app --reload --port 8000` starts cleanly. `POST /api/answer-bank/test-lookup` with `{"field_label": "salary expectation"}` returns correct answer + confidence. `pytest tests/` passes.

---

### Task 1: `services/answer_bank.py`

**Files:**
- Create: `backend/services/answer_bank.py`

- [ ] **Step 1: Write `backend/services/answer_bank.py`**

```python
"""
Answer bank: store and retrieve answers to application form fields.

Lookup priority:
  1. Exact key match (after normalisation)
  2. Fuzzy match via rapidfuzz.token_sort_ratio >= 75
  3. Inference (years of experience from resume dates)
  4. not_found

Always-escalate patterns bypass the bank entirely — caller must escalate.
"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import AnswerBank
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Regex patterns that always trigger human escalation regardless of confidence
ALWAYS_ESCALATE_PATTERNS: list[str] = [
    r"cover.?letter",
    r"why.+(want|join|interested|excited).+(company|role|position|us)",
    r"tell.+about.+yourself",
    r"motivat",
    r"what.+bring.+to",
    r"personal.+statement",
]

_COMPILED_ESCALATE = [re.compile(p, re.IGNORECASE) for p in ALWAYS_ESCALATE_PATTERNS]

# Pre-seeded keys — populated by seed_answer_bank()
SEED_KEYS: list[dict] = [
    {"key": "salary_expectation_sgd",   "value": "SGD 70,000 - 95,000", "format_hint": "range",   "country_tag": "SG"},
    {"key": "salary_expectation_usd",   "value": "USD 80,000 - 120,000","format_hint": "range",   "country_tag": "US"},
    {"key": "graduation_date",           "value": "May 2026",            "format_hint": "text",    "country_tag": None},
    {"key": "work_authorization_sg",     "value": "Yes",                 "format_hint": "yesno",   "country_tag": "SG"},
    {"key": "work_authorization_us",     "value": "No",                  "format_hint": "yesno",   "country_tag": "US"},
    {"key": "work_authorization_uk",     "value": "No",                  "format_hint": "yesno",   "country_tag": "UK"},
    {"key": "notice_period_weeks",       "value": "4",                   "format_hint": "number",  "country_tag": None},
    {"key": "years_experience_python",   "value": "3",                   "format_hint": "number",  "country_tag": None},
    {"key": "years_experience_java",     "value": "2",                   "format_hint": "number",  "country_tag": None},
    {"key": "linkedin_url",              "value": "linkedin.com/in/aryan-ganju","format_hint": "text","country_tag": None},
    {"key": "github_url",                "value": "github.com/AryanG01", "format_hint": "text",    "country_tag": None},
    {"key": "portfolio_url",             "value": "aryanganju.vercel.app","format_hint": "text",   "country_tag": None},
    {"key": "phone_number",              "value": "+65 8940 9011",        "format_hint": "text",    "country_tag": None},
    {"key": "current_location",          "value": "Singapore",           "format_hint": "text",    "country_tag": None},
    {"key": "willing_to_relocate",       "value": "No",                  "format_hint": "yesno",   "country_tag": None},
    {"key": "preferred_work_type",       "value": "hybrid",              "format_hint": "text",    "country_tag": None},
    {"key": "highest_education_level",   "value": "Bachelor's Degree",   "format_hint": "text",    "country_tag": None},
    {"key": "university_name",           "value": "National University of Singapore","format_hint": "text","country_tag": None},
    {"key": "degree_name",               "value": "Bachelor of Computing in Computer Science","format_hint": "text","country_tag": None},
    {"key": "gpa",                       "value": "Not disclosed",       "format_hint": "text",    "country_tag": None},
    {"key": "employment_status",         "value": "student",             "format_hint": "text",    "country_tag": None},
]


class AnswerLookupResult(BaseModel):
    key: str
    value: str
    confidence: float        # 1.0 exact | 0.7–0.99 fuzzy | 0.5–0.69 inferred | 0.0 not_found
    match_type: str          # exact | fuzzy | inferred | not_found
    format_hint: str | None


def _normalise(label: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    label = label.lower()
    label = re.sub(r"[^\w\s]", " ", label)
    return re.sub(r"\s+", " ", label).strip()


def _format_value(value: str, field_type: str, format_hint: str | None) -> str:
    """Reformat a stored value for the specific field_type."""
    if field_type == "number":
        # Extract first integer from value
        nums = re.findall(r"\d[\d,]*", value.replace(",", ""))
        return nums[0] if nums else value
    if field_type in ("checkbox", "yesno", "boolean"):
        lower = value.lower()
        if lower in ("yes", "true", "1"):
            return "Yes"
        if lower in ("no", "false", "0"):
            return "No"
    return value


def should_always_escalate(field_label: str) -> bool:
    """Return True if the label matches any always-escalate pattern."""
    for pattern in _COMPILED_ESCALATE:
        if pattern.search(field_label):
            return True
    return False


async def lookup_answer(
    field_label: str,
    field_type: str,
    session: AsyncSession,
    context: dict | None = None,
) -> AnswerLookupResult:
    """
    Look up an answer for a form field.

    1. Normalise label.
    2. Exact key match against answer_bank table.
    3. Fuzzy match using token_sort_ratio >= 75.
    4. Infer if possible (years of experience).
    5. Apply country_tag filter if context contains country.
    6. Format value for field_type.
    7. Increment usage_count on match.
    """
    normalised = _normalise(field_label)
    country = (context or {}).get("country")

    # Load all rows (answer bank is small — full table scan is fine)
    result = await session.execute(select(AnswerBank))
    rows: list[AnswerBank] = list(result.scalars().all())

    # Filter by country_tag if provided
    def _country_ok(row: AnswerBank) -> bool:
        if row.country_tag is None:
            return True
        if country is None:
            return True
        return row.country_tag.upper() == country.upper()

    candidates = [r for r in rows if _country_ok(r)]

    # 1. Exact match
    for row in candidates:
        if _normalise(row.key) == normalised:
            row.usage_count += 1
            await session.commit()
            return AnswerLookupResult(
                key=row.key,
                value=_format_value(row.value, field_type, row.format_hint),
                confidence=1.0,
                match_type="exact",
                format_hint=row.format_hint,
            )

    # 2. Fuzzy match
    best_row: AnswerBank | None = None
    best_score = 0.0
    for row in candidates:
        score = fuzz.token_sort_ratio(_normalise(row.key), normalised) / 100.0
        if score > best_score:
            best_score = score
            best_row = row

    if best_row and best_score >= 0.75:
        best_row.usage_count += 1
        await session.commit()
        confidence = 0.70 + (best_score - 0.75) * (0.29 / 0.25)  # scale 0.75–1.0 → 0.70–0.99
        return AnswerLookupResult(
            key=best_row.key,
            value=_format_value(best_row.value, field_type, best_row.format_hint),
            confidence=round(min(0.99, confidence), 3),
            match_type="fuzzy",
            format_hint=best_row.format_hint,
        )

    # 3. Infer years of experience
    yoe_match = re.search(r"years?.+experience|experience.+years?", normalised)
    if yoe_match:
        # Very rough inference: count work_experience months from resume
        try:
            yoe_row = next((r for r in rows if r.key == "years_experience_python"), None)
            if yoe_row:
                return AnswerLookupResult(
                    key="inferred_yoe",
                    value=_format_value(yoe_row.value, field_type, "number"),
                    confidence=0.55,
                    match_type="inferred",
                    format_hint="number",
                )
        except Exception:
            pass

    return AnswerLookupResult(
        key=normalised,
        value="",
        confidence=0.0,
        match_type="not_found",
        format_hint=None,
    )


async def seed_answer_bank(session: AsyncSession) -> int:
    """
    Insert pre-seeded answer bank rows if they don't already exist.
    Returns the count of newly inserted rows.
    """
    inserted = 0
    for seed in SEED_KEYS:
        existing = await session.execute(
            select(AnswerBank).where(AnswerBank.key == seed["key"])
        )
        if existing.scalar_one_or_none() is None:
            session.add(AnswerBank(**seed))
            inserted += 1
    await session.commit()
    logger.info("answer_bank_seeded", extra={"inserted": inserted})
    return inserted
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/answer_bank.py
git commit -m "feat: answer bank service — exact/fuzzy lookup, always-escalate patterns, seed"
```

---

### Task 2: `services/confidence_scorer.py`

**Files:**
- Create: `backend/services/confidence_scorer.py`

- [ ] **Step 1: Write `backend/services/confidence_scorer.py`**

```python
"""
Per-field autofill vs escalate decision.

Decision logic:
  1. Always-escalate pattern match → escalate (regardless of confidence)
  2. Answer bank lookup >= autofill_confidence_threshold → autofill
  3. Below threshold + required → escalate
  4. Below threshold + not required → skip
"""
from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.services.answer_bank import lookup_answer, should_always_escalate
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class FieldDecision(BaseModel):
    field_name: str
    field_label: str
    decision: str        # autofill | escalate | skip
    answer: str | None
    confidence: float
    reason: str


async def decide_field(
    field_name: str,
    field_label: str,
    field_type: str,
    is_required: bool,
    session: AsyncSession,
    context: dict | None = None,
) -> FieldDecision:
    """
    Decide whether to autofill, escalate, or skip a form field.

    Args:
        field_name: Internal field identifier (e.g. "salary_expectation").
        field_label: Human-readable label from the form (e.g. "Expected Salary (SGD)").
        field_type: text | dropdown | checkbox | yesno | number
        is_required: Whether the field must be filled.
        session: DB session for answer bank lookup.
        context: Optional dict with keys like 'country', 'company', 'application_id'.
    """
    settings = get_settings()
    threshold = settings.autofill_confidence_threshold

    # 1. Always-escalate check
    if should_always_escalate(field_label):
        logger.info(
            "field_decision_escalate_pattern",
            extra={"field_name": field_name, "app_id": (context or {}).get("application_id")},
        )
        return FieldDecision(
            field_name=field_name,
            field_label=field_label,
            decision="escalate",
            answer=None,
            confidence=0.0,
            reason="Matches always-escalate pattern (personal/motivational content)",
        )

    # 2. Answer bank lookup
    lookup = await lookup_answer(field_label, field_type, session, context)

    if lookup.confidence >= threshold:
        logger.info(
            "field_decision_autofill",
            extra={"field_name": field_name, "match_type": lookup.match_type, "confidence": lookup.confidence},
        )
        return FieldDecision(
            field_name=field_name,
            field_label=field_label,
            decision="autofill",
            answer=lookup.value,
            confidence=lookup.confidence,
            reason=f"{lookup.match_type} match in answer bank",
        )

    # 3. Required but low confidence → escalate
    if is_required:
        logger.info(
            "field_decision_escalate_required",
            extra={"field_name": field_name, "confidence": lookup.confidence},
        )
        return FieldDecision(
            field_name=field_name,
            field_label=field_label,
            decision="escalate",
            answer=lookup.value or None,
            confidence=lookup.confidence,
            reason=f"Required field — confidence {lookup.confidence:.2f} < threshold {threshold}",
        )

    # 4. Optional + low confidence → skip
    logger.info(
        "field_decision_skip",
        extra={"field_name": field_name, "confidence": lookup.confidence},
    )
    return FieldDecision(
        field_name=field_name,
        field_label=field_label,
        decision="skip",
        answer=None,
        confidence=lookup.confidence,
        reason=f"Optional field — confidence {lookup.confidence:.2f} < threshold, leaving blank",
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/confidence_scorer.py
git commit -m "feat: confidence scorer — autofill/escalate/skip decision engine"
```

---

### Task 3: `routers/answer_bank.py`

**Files:**
- Create: `backend/routers/answer_bank.py`

- [ ] **Step 1: Write `backend/routers/answer_bank.py`**

```python
"""
Answer bank CRUD + test-lookup endpoint.

Routes:
  GET    /api/answer-bank               list all answers
  POST   /api/answer-bank               add answer
  PUT    /api/answer-bank/{id}          update answer
  DELETE /api/answer-bank/{id}          delete answer
  POST   /api/answer-bank/test-lookup   test what lookup returns for a label
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import AnswerBank
from backend.schemas import AnswerBankCreate, AnswerBankResponse
from backend.services.answer_bank import lookup_answer

router = APIRouter(prefix="/api/answer-bank", tags=["answer-bank"])


@router.get("", response_model=list[AnswerBankResponse])
async def list_answers(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(AnswerBank).order_by(AnswerBank.key))
    return result.scalars().all()


@router.post("", response_model=AnswerBankResponse, status_code=201)
async def add_answer(body: AnswerBankCreate, session: AsyncSession = Depends(get_session)):
    # Check for duplicate key
    existing = await session.execute(select(AnswerBank).where(AnswerBank.key == body.key))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Key '{body.key}' already exists")
    row = AnswerBank(**body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.put("/{answer_id}", response_model=AnswerBankResponse)
async def update_answer(
    answer_id: int,
    body: AnswerBankCreate,
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AnswerBank, answer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Answer not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{answer_id}", status_code=204)
async def delete_answer(answer_id: int, session: AsyncSession = Depends(get_session)):
    row = await session.get(AnswerBank, answer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Answer not found")
    await session.delete(row)
    await session.commit()


class TestLookupRequest(BaseModel):
    field_label: str
    field_type: str = "text"
    country: str | None = None


from pydantic import BaseModel as _BM  # noqa: E402 — local import for inline model


class _TLReq(_BM):
    field_label: str
    field_type: str = "text"
    country: str | None = None


@router.post("/test-lookup")
async def test_lookup(body: _TLReq, session: AsyncSession = Depends(get_session)):
    """
    Test what the answer bank would return for a given field label.
    Useful for debugging and front-end config verification.
    """
    context = {"country": body.country} if body.country else None
    result = await lookup_answer(body.field_label, body.field_type, session, context)
    return result.model_dump()
```

- [ ] **Step 2: Commit**

```bash
git add backend/routers/answer_bank.py
git commit -m "feat: answer bank router — CRUD + test-lookup endpoint"
```

---

### Task 4: `routers/pending.py`

**Files:**
- Create: `backend/routers/pending.py`

- [ ] **Step 1: Write `backend/routers/pending.py`**

```python
"""
Human review queue for pending clarifications.

Routes:
  GET  /api/pending                     all pending clarifications
  POST /api/pending/{id}/resolve        resolve a field (optionally save to bank)
  POST /api/pending/{id}/skip           skip this field
  POST /api/pending/{id}/reject-app     reject the entire application
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_session
from backend.models import AnswerBank, Application, ApplicationStatus, PendingClarification
from backend.schemas import PendingClarificationResponse, ResolveField

router = APIRouter(prefix="/api/pending", tags=["pending"])


@router.get("", response_model=list[PendingClarificationResponse])
async def list_pending(session: AsyncSession = Depends(get_session)):
    """Return all pending clarifications, newest first."""
    result = await session.execute(
        select(PendingClarification)
        .where(PendingClarification.status == "pending")
        .order_by(PendingClarification.created_at.desc())
    )
    return result.scalars().all()


@router.post("/{clarification_id}/resolve", response_model=PendingClarificationResponse)
async def resolve_clarification(
    clarification_id: int,
    body: ResolveField,
    session: AsyncSession = Depends(get_session),
):
    """
    Resolve a pending field with a human-provided answer.
    Optionally saves the answer to the bank for future reuse.
    """
    row = await session.get(PendingClarification, clarification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Clarification not found")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already {row.status}")

    row.status = "resolved"
    row.resolved_answer = body.answer
    row.resolved_at = datetime.now(timezone.utc)

    if body.save_to_bank and row.field_name:
        # Upsert into answer_bank
        existing = await session.execute(
            select(AnswerBank).where(AnswerBank.key == row.field_name)
        )
        bank_row = existing.scalar_one_or_none()
        if bank_row:
            bank_row.value = body.answer
        else:
            session.add(AnswerBank(key=row.field_name, value=body.answer))

    await session.commit()
    await session.refresh(row)
    return row


@router.post("/{clarification_id}/skip", response_model=PendingClarificationResponse)
async def skip_clarification(
    clarification_id: int,
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(PendingClarification, clarification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Clarification not found")
    row.status = "skipped"
    row.resolved_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/{clarification_id}/reject-app")
async def reject_application(
    clarification_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Reject the entire application linked to this clarification."""
    row = await session.get(PendingClarification, clarification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Clarification not found")

    row.status = "skipped"
    row.resolved_at = datetime.now(timezone.utc)

    app = await session.get(Application, row.application_id)
    if app:
        app.status = ApplicationStatus.SKIPPED

    await session.commit()
    return {"detail": "Application rejected", "application_id": row.application_id}
```

- [ ] **Step 2: Commit**

```bash
git add backend/routers/pending.py
git commit -m "feat: pending clarifications router — resolve/skip/reject"
```

---

### Task 5: `backend/main.py` — FastAPI App Entry Point

**Files:**
- Create: `backend/main.py`

- [ ] **Step 1: Write `backend/main.py`**

```python
"""
Relay — FastAPI application entry point.

Startup: initialises DB tables, seeds answer bank.
Routers registered: answer_bank, pending (Phase 3).
Phases 4–8 will add more routers here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db, AsyncSessionLocal
from backend.routers import answer_bank as answer_bank_router
from backend.routers import pending as pending_router
from backend.utils.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("relay_startup")
    await init_db()
    async with AsyncSessionLocal() as session:
        from backend.services.answer_bank import seed_answer_bank
        seeded = await seed_answer_bank(session)
        if seeded:
            logger.info("answer_bank_seeded_on_startup", extra={"count": seeded})
    yield
    logger.info("relay_shutdown")


app = FastAPI(
    title="Relay",
    description="Autonomous job application agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(answer_bank_router.router)
app.include_router(pending_router.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "relay"}
```

- [ ] **Step 2: Smoke test — server starts**

```bash
python3 -c "from backend.main import app; print('App import OK')"
```

Expected: `App import OK`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: FastAPI main.py — startup, CORS, health endpoint, Phase 3 routers"
```

---

### Task 6: `tests/test_phase3.py` — Pytest Suite

**Files:**
- Create: `tests/test_phase3.py`

- [ ] **Step 1: Write `tests/test_phase3.py`**

```python
"""
Phase 3 tests — answer bank + confidence scorer + API routes.

Tests:
  1.  seed_answer_bank inserts expected keys
  2.  lookup_answer returns exact match with confidence 1.0
  3.  lookup_answer returns fuzzy match for near-synonym label
  4.  lookup_answer returns not_found for unknown label
  5.  should_always_escalate catches cover letter / motivational patterns
  6.  should_always_escalate does NOT flag salary questions
  7.  decide_field returns autofill for high-confidence match
  8.  decide_field returns escalate for always-escalate pattern
  9.  decide_field returns escalate for required field below threshold
  10. decide_field returns skip for optional field below threshold
  11. GET /api/answer-bank returns seeded rows
  12. POST /api/answer-bank adds a new answer
  13. POST /api/answer-bank/test-lookup returns correct result
  14. POST /api/pending/{id}/resolve marks as resolved
  15. POST /api/pending/{id}/reject-app sets application status to skipped
"""
from __future__ import annotations

import os
import pytest
from httpx import ASGITransport, AsyncClient

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# DB setup fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def setup_db():
    """Create all tables and seed answer bank before each test."""
    from backend.database import engine, init_db, Base, AsyncSessionLocal
    from backend.services.answer_bank import seed_answer_bank

    # Drop and recreate for isolation
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        await seed_answer_bank(session)


@pytest.fixture
async def db_session():
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def api_client():
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# 1–4: Answer bank lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_inserts_expected_keys(db_session):
    from sqlalchemy import select
    from backend.models import AnswerBank
    result = await db_session.execute(select(AnswerBank))
    keys = {row.key for row in result.scalars().all()}
    assert "salary_expectation_sgd" in keys
    assert "phone_number" in keys
    assert "work_authorization_sg" in keys
    assert len(keys) >= 20


@pytest.mark.asyncio
async def test_lookup_exact_match(db_session):
    from backend.services.answer_bank import lookup_answer
    result = await lookup_answer("salary_expectation_sgd", "text", db_session)
    assert result.confidence == 1.0
    assert result.match_type == "exact"
    assert "SGD" in result.value


@pytest.mark.asyncio
async def test_lookup_fuzzy_match(db_session):
    from backend.services.answer_bank import lookup_answer
    # "phone" should fuzzy-match "phone_number"
    result = await lookup_answer("phone", "text", db_session)
    assert result.match_type in ("exact", "fuzzy")
    assert result.confidence > 0.5


@pytest.mark.asyncio
async def test_lookup_not_found(db_session):
    from backend.services.answer_bank import lookup_answer
    result = await lookup_answer("xyzzy_unknown_field_abc", "text", db_session)
    assert result.match_type == "not_found"
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# 5–6: Always-escalate patterns
# ---------------------------------------------------------------------------

def test_always_escalate_cover_letter():
    from backend.services.answer_bank import should_always_escalate
    assert should_always_escalate("Cover Letter") is True
    assert should_always_escalate("Upload your cover letter") is True


def test_always_escalate_motivation():
    from backend.services.answer_bank import should_always_escalate
    assert should_always_escalate("Why do you want to join us?") is True
    assert should_always_escalate("Tell us about yourself") is True
    assert should_always_escalate("What motivates you?") is True


def test_always_escalate_does_not_flag_salary():
    from backend.services.answer_bank import should_always_escalate
    assert should_always_escalate("Expected salary (SGD)") is False
    assert should_always_escalate("Years of experience") is False
    assert should_always_escalate("Current location") is False


# ---------------------------------------------------------------------------
# 7–10: Confidence scorer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decide_field_autofill(db_session):
    from backend.services.confidence_scorer import decide_field
    decision = await decide_field(
        field_name="phone",
        field_label="phone_number",
        field_type="text",
        is_required=True,
        session=db_session,
    )
    assert decision.decision == "autofill"
    assert decision.confidence >= 0.85
    assert decision.answer is not None


@pytest.mark.asyncio
async def test_decide_field_escalate_pattern(db_session):
    from backend.services.confidence_scorer import decide_field
    decision = await decide_field(
        field_name="cover_letter",
        field_label="Please write a cover letter",
        field_type="text",
        is_required=True,
        session=db_session,
    )
    assert decision.decision == "escalate"
    assert "always-escalate" in decision.reason.lower()


@pytest.mark.asyncio
async def test_decide_field_escalate_required_low_confidence(db_session):
    from backend.services.confidence_scorer import decide_field
    decision = await decide_field(
        field_name="unknown_required_field",
        field_label="xyzzy_completely_unknown_field_required",
        field_type="text",
        is_required=True,
        session=db_session,
    )
    assert decision.decision == "escalate"


@pytest.mark.asyncio
async def test_decide_field_skip_optional_low_confidence(db_session):
    from backend.services.confidence_scorer import decide_field
    decision = await decide_field(
        field_name="unknown_optional_field",
        field_label="xyzzy_completely_unknown_field_optional",
        field_type="text",
        is_required=False,
        session=db_session,
    )
    assert decision.decision == "skip"


# ---------------------------------------------------------------------------
# 11–13: Answer bank API routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_answer_bank_returns_seeded_rows(api_client):
    resp = await api_client.get("/api/answer-bank")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 20
    keys = {row["key"] for row in data}
    assert "salary_expectation_sgd" in keys


@pytest.mark.asyncio
async def test_post_answer_bank_adds_row(api_client):
    resp = await api_client.post("/api/answer-bank", json={
        "key": "test_custom_key",
        "value": "custom_value",
        "format_hint": "text",
    })
    assert resp.status_code == 201
    assert resp.json()["key"] == "test_custom_key"


@pytest.mark.asyncio
async def test_test_lookup_endpoint(api_client):
    resp = await api_client.post("/api/answer-bank/test-lookup", json={
        "field_label": "salary_expectation_sgd",
        "field_type": "text",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["match_type"] == "exact"
    assert data["confidence"] == 1.0


# ---------------------------------------------------------------------------
# 14–15: Pending clarifications
# ---------------------------------------------------------------------------

@pytest.fixture
async def pending_row(db_session):
    """Insert a test application + pending clarification, return clarification id."""
    from backend.models import Application, PendingClarification
    app = Application(
        id="test-app-001",
        company="TestCo",
        role_title="Engineer",
    )
    db_session.add(app)
    clari = PendingClarification(
        application_id="test-app-001",
        field_name="custom_field",
        field_label="Custom Field",
        question_text="What is your custom answer?",
        suggested_answer="Maybe",
        confidence=0.4,
        status="pending",
    )
    db_session.add(clari)
    await db_session.commit()
    await db_session.refresh(clari)
    return clari.id


@pytest.mark.asyncio
async def test_resolve_clarification(api_client, pending_row):
    resp = await api_client.post(
        f"/api/pending/{pending_row}/resolve",
        json={"answer": "My final answer", "save_to_bank": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"
    assert data["resolved_answer"] == "My final answer"


@pytest.mark.asyncio
async def test_reject_app_sets_skipped(api_client, pending_row, db_session):
    resp = await api_client.post(f"/api/pending/{pending_row}/reject-app")
    assert resp.status_code == 200
    assert resp.json()["application_id"] == "test-app-001"

    from backend.models import Application
    app = await db_session.get(Application, "test-app-001")
    assert app.status == "skipped"
```

- [ ] **Step 2: Run tests**

```bash
python3 -m pytest tests/test_phase3.py -v
```

Expected: 15 tests pass.

- [ ] **Step 3: Run full suite**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -10
```

Expected: 32 passed.

- [ ] **Step 4: Commit and push**

```bash
git add tests/test_phase3.py
git commit -m "test: Phase 3 answer bank + confidence scorer — 15 tests passing"
git push origin main
```

---

## Phase 3 Complete ✓

Deliverables:
- `backend/services/answer_bank.py` — exact/fuzzy/inferred lookup, always-escalate patterns, `seed_answer_bank()`
- `backend/services/confidence_scorer.py` — `decide_field()` returning autofill/escalate/skip
- `backend/routers/answer_bank.py` — CRUD + `/test-lookup`
- `backend/routers/pending.py` — resolve/skip/reject-app
- `backend/main.py` — FastAPI entry point, startup seed, CORS
- `tests/test_phase3.py` — 15 tests passing

**Next:** `plan/phase-04-queue-state-machine.md`
