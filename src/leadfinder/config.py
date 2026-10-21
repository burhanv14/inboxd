"""Application configuration loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Values come from env vars or a `.env` file.

    No secrets are ever hardcoded here.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # --- LLM (NVIDIA NIM) ---
    NVIDIA_API_KEY: str = ""
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_MODEL_LARGE: str = "meta/llama-3.1-8b-instruct"
    NVIDIA_MODEL_SMALL: str = "meta/llama-3.1-8b-instruct"
    NVIDIA_TEMPERATURE_PLANNER: float = 0.2
    NVIDIA_TEMPERATURE_SCORER: float = 0.0
    NVIDIA_TEMPERATURE_PARSER: float = 0.0
    NVIDIA_TEMPERATURE_PATTERNS: float = 0.0
    NVIDIA_TEMPERATURE_REPAIR: float = 0.0

    # --- Google Custom Search ---
    CSE_API_KEY: str = ""
    CSE_ID: str = ""

    # --- Concurrency / limits ---
    SEARCH_CONCURRENCY: int = 8
    LLM_CONCURRENCY: int = 8

    # --- Runtime defaults ---
    DEFAULT_MAX_RESULTS: int = 50
    DEFAULT_MIN_SCORE: int = 7
    DEFAULT_MIN_RESULTS: int = 10
    DEFAULT_PAGES: int = 2

    # --- Misc ---
    LOG_LEVEL: str = "INFO"
    PROMPT_VERSION: str = "1.0.0"

    def validate_startup(self) -> list[str]:
        """Return a list of human-friendly startup problems (empty = OK)."""
        problems: list[str] = []
        if not self.NVIDIA_API_KEY:
            problems.append(
                "NVIDIA_API_KEY is not set. Add it to .env (see .env.example)."
            )
        if not self.CSE_API_KEY:
            problems.append(
                "CSE_API_KEY is not set. Add it to .env (see .env.example)."
            )
        if not self.CSE_ID:
            problems.append(
                "CSE_ID is not set. Add it to .env (see .env.example)."
            )
        return problems


settings = Settings()
