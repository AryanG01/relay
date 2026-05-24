"""
Select and reorder resume bullets for a specific application.

For each role/project: score ALL bullets in one LLM call (batch per role),
select top max_bullets_per_role, reorder by relevance score DESC.
Reorder projects by relevance, skills within each category by JD presence.
"""
from __future__ import annotations

import json

from backend.schemas import MasterResume, MatchResult, ParsedJD, SelectedResume
from backend.utils.llm import call_llm
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_SYSTEM = (
    "You are a resume optimisation assistant. "
    "Score resume bullets for relevance to a job description. "
    "Return ONLY valid JSON. No markdown."
)

_BULLET_SCORE_PROMPT = """\
Score each bullet 0.0–1.0 for relevance to this job.

Job required skills: {required_skills}
Job responsibilities: {responsibilities}
Job domain: {domain}

Bullets to score:
{bullets_json}

Return JSON array (same order as input):
[{{"id": "...", "score": 0.0}}, ...]
"""


async def _score_bullets(bullets: list[dict], parsed_jd: ParsedJD) -> dict[str, float]:
    """Return {bullet_id: score} for all bullets in one LLM call."""
    if not bullets:
        return {}

    prompt = _BULLET_SCORE_PROMPT.format(
        required_skills=", ".join(parsed_jd.required_skills[:15]),
        responsibilities="\n".join(f"- {r}" for r in parsed_jd.responsibilities[:6]),
        domain=parsed_jd.domain,
        bullets_json=json.dumps(
            [{"id": b["id"], "text": b["text"][:200]} for b in bullets], indent=2
        ),
    )

    raw = await call_llm(prompt, system=_SYSTEM, max_tokens=500, expect_json=True)
    scored: list[dict] = json.loads(raw)
    return {item["id"]: float(item.get("score", 0.5)) for item in scored}


def _reorder_skills(skills: dict, jd_skills: set[str]) -> dict:
    """Within each skill category, put JD-mentioned skills first."""
    result = {}
    for category, items in skills.items():
        if not isinstance(items, list):
            result[category] = items
            continue
        jd_first = [s for s in items if s.lower() in jd_skills]
        rest = [s for s in items if s.lower() not in jd_skills]
        result[category] = jd_first + rest
    return result


async def select_bullets(
    master_resume: MasterResume,
    parsed_jd: ParsedJD,
    match_result: MatchResult,
    max_bullets_per_role: int = 4,
) -> SelectedResume:
    """
    Build a SelectedResume tailored for a specific JD.

    1. Score all bullets per role/project via LLM (one call per role).
    2. Select top max_bullets_per_role bullets per role.
    3. Reorder bullets: most relevant first within each role.
    4. Reorder projects by average bullet score.
    5. Reorder skills: JD-relevant first within each category.
    6. Set section_order based on role domain.
    """
    jd_skills = {s.lower() for s in parsed_jd.required_skills + parsed_jd.tech_stack}

    # --- Score and select work experience bullets ---
    selected_exp = []
    for exp in master_resume.work_experience:
        bullets_raw = [b.model_dump() for b in exp.bullets]
        scores = await _score_bullets(bullets_raw, parsed_jd)

        sorted_bullets = sorted(
            bullets_raw,
            key=lambda b: scores.get(b["id"], 0.0),
            reverse=True,
        )
        selected = sorted_bullets[:max_bullets_per_role]
        relevance = sum(scores.get(b["id"], 0) for b in selected) / max(len(selected), 1)

        exp_dict = exp.model_dump()
        exp_dict["bullets"] = selected
        exp_dict["_relevance"] = relevance
        selected_exp.append(exp_dict)

    # --- Score and select project bullets ---
    scored_projects = []
    for proj in master_resume.projects:
        bullets_raw = [b.model_dump() for b in proj.bullets]
        scores = await _score_bullets(bullets_raw, parsed_jd)

        sorted_bullets = sorted(
            bullets_raw,
            key=lambda b: scores.get(b["id"], 0.0),
            reverse=True,
        )
        proj_dict = proj.model_dump()
        proj_dict["bullets"] = sorted_bullets[:max_bullets_per_role]
        avg_score = sum(scores.values()) / max(len(scores), 1)
        scored_projects.append((avg_score, proj_dict))

    # Reorder projects by relevance
    scored_projects.sort(key=lambda x: x[0], reverse=True)
    selected_projects = [p for _, p in scored_projects]

    # --- Reorder skills ---
    skills_dict = master_resume.skills.model_dump()
    reordered_skills = _reorder_skills(skills_dict, jd_skills)

    # --- Section order: tech roles → projects before education ---
    section_order = ["work_experience", "projects", "skills", "education"]
    if parsed_jd.domain in ("finance", "trading"):
        section_order = ["work_experience", "skills", "projects", "education"]

    logger.info(
        "bullets_selected",
        extra={"exp_count": len(selected_exp), "project_count": len(selected_projects)},
    )

    return SelectedResume(
        personal=master_resume.personal.model_dump(),
        summary=master_resume.summary,
        work_experience=[{k: v for k, v in e.items() if k != "_relevance"} for e in selected_exp],
        education=[e.model_dump() for e in master_resume.education],
        skills=reordered_skills,
        projects=selected_projects,
        certifications=[c.model_dump() for c in master_resume.certifications],
        section_order=section_order,
    )
