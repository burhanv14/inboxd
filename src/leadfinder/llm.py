"""Async LLM wrapper backed by AsyncOpenAI pointed at NVIDIA NIM.

Provides json_call() with tenacity retries, aiolimiter rate limiting,
per-call timing, one-shot repair on invalid JSON, and graceful skip.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from aiolimiter import AsyncLimiter
from openai import APIConnectionError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from leadfinder.config import settings
from leadfinder.logging import get_logger

logger = get_logger(__name__)

# Tolerant JSON extraction: strip code fences, then take first balanced object/array.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_BALANCED_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")


class LLMError(RuntimeError):
    """Raised when an LLM call fails after retries and repair."""


def _extract_json(text: str) -> Any:
    """Extract JSON from model output, tolerating code fences and extra prose."""
    if not text:
        raise LLMError("empty model output")
    cleaned = text.strip()
    m = _JSON_FENCE_RE.search(cleaned)
    if m:
        cleaned = m.group(1).strip()
    # find first balanced JSON object or array
    m2 = _BALANCED_RE.search(cleaned)
    if m2:
        cleaned = m2.group(0)
    return json.loads(cleaned)


class LLM:
    """AsyncOpenAI client wrapper for NVIDIA NIM with retries, limiting, repair."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        requests_per_second: float = 8.0,
    ) -> None:
        self._api_key = api_key or settings.NVIDIA_API_KEY
        self._base_url = base_url or settings.NVIDIA_BASE_URL
        self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        self._limiter = AsyncLimiter(max_rate=requests_per_second, time_period=1.0)
        self._log = logger.bind(component="llm")

    @property
    def client(self) -> AsyncOpenAI:
        return self._client

    async def _retryable_completion(  # noqa: PLR0913, PLR0917 - several completion parameters
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        response_format: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> str:
        """One completion attempt with tenacity retry on transient errors."""

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=10),
            retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
            reraise=True,
        )
        async def _call() -> str:
            async with self._limiter:
                start = time.monotonic()
                try:
                    resp = await self._client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        response_format=response_format,
                        extra_body=extra_body,
                        timeout=timeout,
                    )
                finally:
                    self._log.debug(
                        "llm.call",
                        model=model,
                        duration_ms=round((time.monotonic() - start) * 1000, 1),
                    )
            content = resp.choices[0].message.content or ""
            return content

        return await _call()

    async def json_call(  # noqa: PLR0913 - many optional call parameters
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        response_format: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout: float = 30.0,
        repair_model: str | None = None,
        repair_temperature: float = 0.0,
    ) -> Any:
        """Get validated JSON from the model.

        Tries response_format json_object first; falls back to prompt-enforced
        JSON + tolerant extraction. On pydantic/JSON failure, attempts ONE
        repair retry with the repair model (default: small model), then raises
        LLMError so the caller can gracefully skip.
        """
        repair_model = repair_model or settings.NVIDIA_MODEL_SMALL
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        last_text = ""
        # Primary attempt (with optional json_object / guided JSON)
        try:
            text = await self._retryable_completion(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format=response_format,
                extra_body=extra_body,
                timeout=timeout,
            )
            last_text = text
            return _extract_json(text)
        except Exception as exc:
            self._log.warning(
                "llm.primary_failed", model=model, error=str(exc)[:200]
            )

        # Repair attempt: ask the small model to re-emit pure JSON
        repair_user = (
            "The previous model output was not valid JSON. "
            "Re-output ONLY valid JSON, no prose, no code fences, matching the requested schema.\n\n"
            f"Original system instruction:\n{system}\n\n"
            f"Original user request:\n{user}\n\n"
            f"Previous (invalid) output:\n{last_text}"
        )
        try:
            text = await self._retryable_completion(
                model=repair_model,
                messages=[
                    {"role": "system", "content": "You output valid JSON only."},
                    {"role": "user", "content": repair_user},
                ],
                temperature=repair_temperature,
                response_format={"type": "json_object"},
                timeout=timeout,
            )
            return _extract_json(text)
        except Exception as exc:
            self._log.warning(
                "llm.repair_failed", model=repair_model, error=str(exc)[:200]
            )
            raise LLMError(
                f"LLM JSON call failed after repair: {exc}"
            ) from exc
