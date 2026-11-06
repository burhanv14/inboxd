"""Keyword-based relevance scoring of candidates against an Intent.

The LLM has been removed. Scoring is now done with a deterministic keyword
heuristic (the previous `_fallback_score` logic). The external LLM client
(connected via MCP) can perform its own scoring if desired.
"""

from __future__ import annotations

import asyncio
import json

from log import get_logger
from models import (
    Candidate,
    Intent,
    RegionJudgment,
    RoleJudgment,
    ScoredCandidate,
)

logger = get_logger(__name__)

BATCH_SIZE = 20
BATCH_CONCURRENCY = 8


# --- Keyword-based scorer (deterministic, no LLM) ---

_HIRING_ROLE_KEYWORDS = (
    "recruiter", "talent acquisition", "sourcer", "talent partner",
    "hr business partner", "hiring manager", "technical recruiter",
    "recruiting coordinator", "head of talent",
)
_CANDIDATE_SIDE_KEYWORDS = (
    "data analyst", "software engineer", "product manager", "accountant",
    "data scientist", "ml engineer", "machine learning engineer",
    "open to work", "looking for a job", "seeking opportunities",
    "student", "intern", "candidate",
)
_NEGATIVE_KEYWORDS = (
    "ex-", "formerly", "ex ", "staffing agency", "rpo",
    "recruitment process outsourcing", "general staffing",
)


def _keyword_score(cand: Candidate, intent: Intent) -> ScoredCandidate:
    """Deterministic keyword relevance score (no LLM)."""
    text = f"{cand.headline} {cand.position} {cand.raw_snippet} {cand.company}".lower()

    is_hiring = any(k in text for k in _HIRING_ROLE_KEYWORDS)
    is_candidate_side = any(k in text for k in _CANDIDATE_SIDE_KEYWORDS)
    is_negative = any(k in text for k in _NEGATIVE_KEYWORDS)

    region_match = any(
        r.lower() in text for r in (intent.region_terms or [])
    ) if intent.region_terms else None
    title_match = (intent.job_title or "").lower() in text if intent.job_title else None

    score = 0
    if is_hiring:
        score += 6
    if is_candidate_side:
        score -= 5
    if is_negative:
        score -= 4
    if title_match:
        score += 2
    if region_match:
        score += 2
    score = max(0, min(10, score))

    is_target_role = (
        "true" if is_hiring and not is_candidate_side else
        ("false" if is_candidate_side else "unknown")
    )
    region_ok = (
        "true" if region_match else
        ("false" if region_match is False else "unknown")
    )

    return ScoredCandidate(
        id=cand.id, match=score,
        is_target_role=is_target_role, region_ok=region_ok,
        reason=f"keyword score (hiring={is_hiring}, region={region_ok})",
        name=cand.name, headline=cand.headline, position=cand.position,
        company=cand.company, location_hint=cand.location_hint,
        linkedin_url=cand.linkedin_url,
        raw_title=cand.raw_title, raw_snippet=cand.raw_snippet,
    )


class Scorer:
    """Scores candidates in batches using keyword heuristics (no LLM)."""

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(BATCH_CONCURRENCY)
        self._log = logger.bind(component="scorer")

    async def score(
        self,
        intent_json: str,
        candidates: list[Candidate],
        batch_size: int = BATCH_SIZE,
    ) -> list[ScoredCandidate]:
        if not candidates:
            return []
        try:
            intent = Intent(**json.loads(intent_json))
        except Exception:
            intent = None

        batches: list[list[Candidate]] = [
            candidates[i : i + batch_size] for i in range(0, len(candidates), batch_size)
        ]
        self._log.info("scorer.start", n_candidates=len(candidates), n_batches=len(batches))

        async def _score_batch(batch: list[Candidate]) -> list[ScoredCandidate]:
            async with self._sem:
                return await self._score_one_batch(batch, intent)

        results = await asyncio.gather(*[_score_batch(b) for b in batches])
        merged: list[ScoredCandidate] = []
        for r in results:
            merged.extend(r)
        return merged

    async def _score_one_batch(
        self, batch: list[Candidate], intent: Intent | None = None
    ) -> list[ScoredCandidate]:
        # Keyword scoring only (no LLM).
        if intent is not None:
            return [_keyword_score(c, intent) for c in batch]
        return [
            ScoredCandidate(
                id=c.id, match=0,
                is_target_role=RoleJudgment.UNKNOWN,
                region_ok=RegionJudgment.UNKNOWN, reason="no intent available",
                name=c.name, headline=c.headline, position=c.position,
                company=c.company, location_hint=c.location_hint,
                linkedin_url=c.linkedin_url,
                raw_title=c.raw_title, raw_snippet=c.raw_snippet,
            )
            for c in batch
        ]
