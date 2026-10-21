"""Google Custom Search API search provider."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from leadfinder.config import settings
from leadfinder.logging import get_logger
from leadfinder.models import SearchHit

logger = get_logger(__name__)

GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
STATUS_TOO_MANY_REQUESTS = 429
RESULTS_PER_PAGE = 10


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {STATUS_TOO_MANY_REQUESTS, 500, 502, 503, 504}
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    return False


class GoogleCSEProvider:
    """Google Custom Search API provider.

    GET https://www.googleapis.com/customsearch/v1
      ?key=API_KEY&cx=SEARCH_ENGINE_ID&q=QUERY&start=START&num=NUM
    """

    def __init__(  # noqa: PLR0913, PLR0917 - provider configuration needs several parameters
        self,
        api_key: str | None = None,
        cx: str | None = None,
        base_url: str = GOOGLE_CSE_URL,
        concurrency: int = 8,
        timeout: float = 10.0,
        requests_per_second: float = 8.0,
    ) -> None:
        self._api_key = api_key or settings.CSE_API_KEY
        self._cx = cx or settings.CSE_ID
        self._base_url = base_url
        self._timeout = httpx.Timeout(timeout)
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=50,
            keepalive_expiry=30.0,
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Content-Type": "application/json"},
            timeout=self._timeout,
            limits=limits,
        )
        self._limiter = __import__("aiolimiter").AsyncLimiter(
            max_rate=requests_per_second, time_period=1.0
        )
        self._log = logger.bind(component="search.google_cse")

    async def search(
        self, query: str, page: int, region: str | None = None
    ) -> list[SearchHit]:
        if not self._api_key:
            self._log.warning("search.missing_api_key")
            return []
        if not self._cx:
            self._log.warning("search.missing_cse_id")
            return []

        start = (max(1, page) - 1) * RESULTS_PER_PAGE + 1
        params: dict[str, Any] = {
            "key": self._api_key,
            "cx": self._cx,
            "q": query,
            "start": start,
            "num": min(RESULTS_PER_PAGE, 10),
        }
        if region:
            params["gl"] = region

        @retry(
            stop=stop_after_attempt(4),
            wait=wait_exponential_jitter(initial=1, max=20),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        async def _call() -> list[SearchHit]:
            async with self._limiter:
                start_t = time.monotonic()
                try:
                    resp = await self._client.get("", params=params)
                    if (
                        resp.status_code == STATUS_TOO_MANY_REQUESTS
                        and resp.headers.get("Retry-After")
                    ):
                        try:
                            await asyncio.sleep(float(resp.headers["Retry-After"]))
                        except (ValueError, TypeError):
                            pass
                    resp.raise_for_status()
                finally:
                    self._log.debug(
                        "search.call",
                        query=query[:60],
                        page=page,
                        status=getattr(resp, "status_code", None),
                        duration_ms=round((time.monotonic() - start_t) * 1000, 1),
                    )
                data = resp.json()
            return self._parse(data, query)

        try:
            return await _call()
        except Exception as exc:
            self._log.warning(
                "search.query_failed", query=query[:60], error=str(exc)[:200]
            )
            return []

    def _parse(self, data: dict[str, Any], source_query: str) -> list[SearchHit]:
        hits: list[SearchHit] = []
        items = data.get("items") or []
        # Google CSE doesn't return a global position, so compute from start index.
        # We don't know the exact start here, so use sequential positions per page.
        for idx, item in enumerate(items):
            title = item.get("title") or ""
            snippet = item.get("snippet") or ""
            url = item.get("link") or ""
            if not url:
                continue
            hits.append(
                SearchHit(
                    title=title,
                    snippet=snippet,
                    url=url,
                    position=idx + 1,
                    source_query=source_query,
                )
            )
        return hits

    async def close(self) -> None:
        await self._client.aclose()
