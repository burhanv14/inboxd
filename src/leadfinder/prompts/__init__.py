"""Prompt loader: versioned markdown prompt templates."""

from __future__ import annotations

from functools import cache
from pathlib import Path

PROMPT_VERSION = "1.0.0"
_PROMPTS_DIR = Path(__file__).resolve().parent


@cache
def load(name: str) -> str:
    """Load a prompt template by filename (e.g. 'scorer.md'). Cached in memory."""
    path = _PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")
