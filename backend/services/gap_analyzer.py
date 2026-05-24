"""
Simple skill gap analysis — identifies required JD skills absent from the resume.
This is a lightweight companion to match_scorer; no LLM calls needed.
"""
from __future__ import annotations

from pydantic import BaseModel
from rapidfuzz import fuzz

from backend.schemas import MasterResume, ParsedJD


class GapReport(BaseModel):
    hard_gaps: list[str]   # required skills with no resume evidence
    soft_gaps: list[str]   # preferred skills with no resume evidence
    covered: list[str]     # required skills with evidence


def analyze_gaps(resume: MasterResume, parsed_jd: ParsedJD) -> GapReport:
    """
    Return a GapReport comparing parsed_jd requirements against the resume.
    Uses fuzzy matching (token_sort_ratio >= 80) to handle synonyms.
    """
    resume_terms: set[str] = set()
    for cat in vars(resume.skills).values():
        if isinstance(cat, list):
            resume_terms.update(t.lower() for t in cat)
    for exp in resume.work_experience:
        for b in exp.bullets:
            resume_terms.update(s.lower() for s in b.skills)
    for proj in resume.projects:
        resume_terms.update(t.lower() for t in proj.tech_stack)

    def _has_evidence(skill: str) -> bool:
        sl = skill.lower()
        if sl in resume_terms:
            return True
        return any(fuzz.token_sort_ratio(sl, rt) >= 80 for rt in resume_terms)

    hard_gaps, covered = [], []
    for skill in parsed_jd.required_skills:
        if _has_evidence(skill):
            covered.append(skill)
        else:
            hard_gaps.append(skill)

    soft_gaps = [s for s in parsed_jd.preferred_skills if not _has_evidence(s)]

    return GapReport(hard_gaps=hard_gaps, soft_gaps=soft_gaps, covered=covered)
