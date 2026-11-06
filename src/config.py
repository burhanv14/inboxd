"""Application configuration loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Values come from env vars or a `.env` file.

    No secrets are ever hardcoded here.

    The LLM is no longer embedded in this application. Lead discovery is exposed
    as an MCP server (see src/mcp_server.py) so the user can connect
    their own LLM client (opencode, claude, codex, antigravity, etc.).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # --- Google Custom Search ---
    CSE_API_KEY: str = ""
    CSE_ID: str = ""

    # --- MCP Server (HTTP only) ---
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8765

    # --- Concurrency / limits ---
    SEARCH_CONCURRENCY: int = 8

    # --- Runtime defaults ---
    DEFAULT_MAX_RESULTS: int = 50
    DEFAULT_MIN_SCORE: int = 7
    DEFAULT_PAGES: int = 2

    # --- Misc ---
    LOG_LEVEL: str = "INFO"
    PROMPT_VERSION: str = "1.0.0"

    def validate_startup(self) -> list[str]:
        """Return a list of human-friendly startup problems (empty = OK)."""
        problems: list[str] = []
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
