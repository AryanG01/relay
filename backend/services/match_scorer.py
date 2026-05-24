"""
Score how well the master resume matches a parsed JD (0–100).

Fast pre-filter: keyword overlap ratio. If < 0.25, return score=0 (no LLM call).
Full score: weighted combination of required_coverage, experience_relevance,
domain_alignment, seniority_fit.

Weights: required_coverage*0.40 + experience_relevance*0.30
       + domain_alignment*0.20 + seniority_fit*0.10
"""
from __future__ import annotations

import json

from rapidfuzz import fuzz

from backend.schemas import MasterResume, MatchResult, ParsedJD
from backend.utils.llm import call_llm
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_SENIORITY_RANK = {"junior": 1, "mid": 2, "senior": 3, "lead": 4, "unknown": 2}

_SEMANTIC_SYSTEM = (
    "You are a resume-to-job-description relevance scorer. "
    "Return ONLY valid JSON. No markdown, no explanation."
)

_SEMANTIC_PROMPT = """\
Score the following resume experience against the job requirements.

Resume work experience summary:
{experience_summary}

Job responsibilities:
{responsibilities}

Job domain: {domain}
Resume domains: {resume_domains}

Return JSON:
{{
  "experience_relevance": 0.0,
  "domain_alignment": 0.0,
  "reasoning": "one sentence"
}}

experience_relevance: 0.0–1.0 — how well the experience matches the responsibilities.
domain_alignment: 0.0–1.0 — how well the resume domain matches the job domain.
"""


def _extract_resume_skills(resume: MasterResume) -> set[str]:
    """Flatten all skills from bullets + skills section into a lowercase set."""
    skills: set[str] = set()
    for cat in [
        resume.skills.languages,
        resume.skills.frameworks,
        resume.skills.tools,
        resume.skills.databases,
        resume.skills.domains,
        resume.skills.other,
    ]:
        skills.update(s.lower() for s in cat)

    for exp in resume.work_experience:
        for bullet in exp.bullets:
            skills.update(s.lower() for s in bullet.skills)

    for proj in resume.projects:
        for bullet in proj.bullets:
            skills.update(s.lower() for s in bullet.skills)
        skills.update(t.lower() for t in proj.tech_stack)

    return skills


def _fuzzy_match(skill: str, resume_skills: set[str], threshold: int = 80) -> str:
    """Return 'exact', 'fuzzy', or 'missing'."""
    sl = skill.lower()
    if sl in resume_skills:
        return "exact"
    for rs in resume_skills:
        if fuzz.token_sort_ratio(sl, rs) >= threshold:
            return "fuzzy"
    return "missing"


def _infer_user_yoe(resume: MasterResume) -> float:
    """Rough years-of-experience from work_experience dates."""
    from datetime import date

    total_months = 0
    for exp in resume.work_experience:
        try:
            start_y, start_m = map(int, exp.start_date.split("-"))
            if exp.end_date:
                end_y, end_m = map(int, exp.end_date.split("-"))
            else:
                today = date.today()
                end_y, end_m = today.year, today.month
            total_months += (end_y - start_y) * 12 + (end_m - start_m)
        except (ValueError, AttributeError):
            continue
    return round(total_months / 12, 1)


async def score_match(resume: MasterResume, parsed_jd: ParsedJD) -> MatchResult:
    """Score 0–100 how well the resume matches the JD."""
    resume_skills = _extract_resume_skills(resume)

    # --- Fast pre-filter ---
    all_jd_terms = {s.lower() for s in parsed_jd.required_skills + parsed_jd.tech_stack}
    if all_jd_terms:
        overlap = len(all_jd_terms & resume_skills) / len(all_jd_terms)
        if overlap < 0.25:
            logger.info("match_prefilter_fail", extra={"overlap": round(overlap, 3)})
            return MatchResult(
                overall_score=0.0,
                required_coverage=0.0,
                experience_relevance=0.0,
                domain_alignment=0.0,
                seniority_fit=0.0,
                missing_required=parsed_jd.required_skills,
            )

    # --- Required coverage ---
    strong, partial, missing = [], [], []
    for skill in parsed_jd.required_skills:
        result = _fuzzy_match(skill, resume_skills)
        if result == "exact":
            strong.append(skill)
        elif result == "fuzzy":
            partial.append(skill)
        else:
            missing.append(skill)

    total_required = len(parsed_jd.required_skills) or 1
    required_coverage = (len(strong) + 0.5 * len(partial)) / total_required

    # --- Semantic scoring via LLM ---
    exp_summary = " | ".join(
        f"{e.company} ({e.role}): " + "; ".join(b.text[:80] for b in e.bullets[:2])
        for e in resume.work_experience[:3]
    )
    resume_domains = list({b.domain for e in resume.work_experience for b in e.bullets})

    prompt = _SEMANTIC_PROMPT.format(
        experience_summary=exp_summary[:2000],
        responsibilities="\n".join(f"- {r}" for r in parsed_jd.responsibilities[:6]),
        domain=parsed_jd.domain,
        resume_domains=", ".join(resume_domains),
    )

    raw = await call_llm(prompt, system=_SEMANTIC_SYSTEM, max_tokens=300, expect_json=True)
    semantic = json.loads(raw)
    experience_relevance = float(semantic.get("experience_relevance", 0.5))
    domain_alignment = float(semantic.get("domain_alignment", 0.5))

    # --- Seniority fit ---
    user_yoe = _infer_user_yoe(resume)
    jd_rank = _SENIORITY_RANK.get(parsed_jd.role_level, 2)
    user_rank = 1 if user_yoe < 2 else (2 if user_yoe < 5 else 3)
    seniority_fit = max(0.0, 1.0 - abs(jd_rank - user_rank) * 0.3)

    # --- Weighted final score ---
    overall = (
        required_coverage * 0.40
        + experience_relevance * 0.30
        + domain_alignment * 0.20
        + seniority_fit * 0.10
    ) * 100
    overall = round(min(100.0, max(0.0, overall)), 1)

    logger.info(
        "match_scored",
        extra={
            "overall": overall,
            "required_coverage": round(required_coverage, 3),
            "missing_count": len(missing),
        },
    )

    return MatchResult(
        overall_score=overall,
        required_coverage=required_coverage,
        experience_relevance=experience_relevance,
        domain_alignment=domain_alignment,
        seniority_fit=seniority_fit,
        missing_required=missing,
        partial_required=partial,
        strong_matches=strong,
    )
