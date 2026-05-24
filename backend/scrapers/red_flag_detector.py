"""
Red flag detection for job listings.

Checks a JobListing against configured filters before it enters the pipeline:
  - Excluded company names
  - Visa sponsorship required when we can't sponsor
  - Contract-only roles when excluded_contract_only=True
  - Salary below minimum (if parseable)
  - Blacklisted keywords in title/JD
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.config import get_settings
from backend.scrapers.base_scraper import JobListing

# Keywords that indicate visa sponsorship is NOT available to us
_VISA_DENIED_PATTERNS = [
    r"must be.*citizen",
    r"only.*citizens",
    r"no.*sponsorship",
    r"sponsorship.*not.*available",
    r"us.*citizen.*only",
    r"clearance.*required",
    r"security.*clearance",
]

# Keywords that flag contract/temp roles
_CONTRACT_PATTERNS = [
    r"\bcontract\b",
    r"\bfreelance\b",
    r"\btemporary\b",
    r"\btemp\b",
    r"\bc2c\b",
    r"\bcorp.?to.?corp\b",
]

# Universally excluded companies (case-insensitive)
_DEFAULT_EXCLUDED_COMPANIES = [
    "staffing",
    "recruiter",
    "talent agency",
    "outsource",
]

# Titles we never want (e.g. pure sales, non-tech)
_EXCLUDED_TITLE_PATTERNS = [
    r"\bsales\b",
    r"\bmarketing\b",
    r"\baccountant\b",
    r"\bhr\b",
    r"\bhuman resources\b",
]

_COMPILED_VISA = [re.compile(p, re.IGNORECASE) for p in _VISA_DENIED_PATTERNS]
_COMPILED_CONTRACT = [re.compile(p, re.IGNORECASE) for p in _CONTRACT_PATTERNS]
_COMPILED_TITLE = [re.compile(p, re.IGNORECASE) for p in _EXCLUDED_TITLE_PATTERNS]


@dataclass
class RedFlagResult:
    is_red_flag: bool
    reasons: list[str] = field(default_factory=list)


def detect_red_flags(listing: JobListing) -> RedFlagResult:
    """
    Analyse a JobListing for disqualifying signals.

    Returns RedFlagResult(is_red_flag=True, reasons=[...]) if any flag fires.
    """
    settings = get_settings()
    reasons: list[str] = []
    text = f"{listing.title} {listing.company} {listing.raw_jd}".lower()

    # 1. Excluded company names
    for excl in _DEFAULT_EXCLUDED_COMPANIES:
        if excl.lower() in listing.company.lower():
            reasons.append(f"Excluded company keyword: {excl!r}")
            break

    # 2. Visa/sponsorship check
    for pat in _COMPILED_VISA:
        if pat.search(text):
            reasons.append(f"Visa/sponsorship red flag: {pat.pattern!r}")
            break

    # 3. Contract-only filter
    if settings.exclude_contract_only:
        for pat in _COMPILED_CONTRACT:
            if pat.search(listing.title) or pat.search(listing.raw_jd):
                reasons.append(f"Contract role: {pat.pattern!r}")
                break

    # 4. Non-tech title filter
    for pat in _COMPILED_TITLE:
        if pat.search(listing.title):
            reasons.append(f"Non-tech title pattern: {pat.pattern!r}")
            break

    # 5. Salary floor check (best-effort parse)
    if listing.salary_text:
        nums = re.findall(r"\d[\d,]*", listing.salary_text.replace(",", ""))
        if nums:
            try:
                salary_val = int(nums[0])
                # Rough heuristic: if value < 10000 treat as monthly, annualise
                if salary_val < 10_000:
                    salary_val *= 12
                if "usd" in listing.salary_text.lower():
                    if salary_val < settings.salary_usd_min:
                        reasons.append(f"Salary {salary_val} below USD minimum {settings.salary_usd_min}")
                else:
                    if salary_val < settings.salary_sgd_min:
                        reasons.append(f"Salary {salary_val} below SGD minimum {settings.salary_sgd_min}")
            except (ValueError, IndexError):
                pass

    return RedFlagResult(is_red_flag=bool(reasons), reasons=reasons)
