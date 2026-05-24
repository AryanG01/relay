"""
Settings loaded from two sources (in priority order):
  1. Environment variables / .env file  ← secrets only (API keys)
  2. data/config.yaml                   ← everything else (limits, windows, filters)

Pydantic-settings merges both; env vars always win on conflict.
"""
from __future__ import annotations

import os
from functools import lru_cache

import yaml
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
        if not isinstance(v, dict) and not isinstance(v, list):
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
