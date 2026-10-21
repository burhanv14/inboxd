"""Search provider abstract base class (Google Custom Search)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from leadfinder.models import SearchHit


class SearchProvider(ABC):
    """Abstract search provider: returns SearchHit lists (title + snippet only)."""

    @abstractmethod
    async def search(
        self, query: str, page: int, region: str | None = None
    ) -> list[SearchHit]:
        """Run one query page; return hits (max ~10 per page)."""

    @abstractmethod
    async def close(self) -> None:
        """Close the underlying HTTP client."""
