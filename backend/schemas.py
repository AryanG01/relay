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
    resolved_answer: Optional[str] = None

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
