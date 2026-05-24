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
