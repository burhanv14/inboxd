"""Batched LLM relevance scoring of candidates against an Intent."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from leadfinder.config import settings
from leadfinder.llm import LLM, LLMError
from leadfinder.logging import get_logger
from leadfinder.models import (
    Candidate,
    Intent,
    RegionJudgment,
    RoleJudgment,
    ScoredCandidate,
)
from leadfinder.prompts import load

logger = get_logger(__name__)

BATCH_SIZE = 20
BATCH_CONCURRENCY = 8


def _render_candidates(cands: list[Candidate]) -> list[dict[str, str]]:
    return [
        {
            "id": c.id,
            "name": c.name,
            "headline": c.headline,
            "snippet": c.raw_snippet[:220],
        }
        for c in cands
    ]


def _merge_scores(
    candidates: list[Candidate], scores: list[dict[str, Any]]
) -> list[ScoredCandidate]:
    by_id = {c.id: c for c in candidates}
    scored: list[ScoredCandidate] = []
    for s in scores:
        cid = str(s.get("id", ""))
        cand = by_id.get(cid)
        if cand is None:
            continue
        scored.append(
            ScoredCandidate(
                id=cid,
                match=int(s.get("match", 0)),
                is_target_role=RoleJudgment(str(s.get("is_target_role", "unknown"))),
                region_ok=RegionJudgment(str(s.get("region_ok", "unknown"))),
                reason=str(s.get("reason", ""))[:200],
                name=cand.name,
                headline=cand.headline,
                position=cand.position,
                company=cand.company,
                location_hint=cand.location_hint,
                linkedin_url=cand.linkedin_url,
                raw_title=cand.raw_title,
                raw_snippet=cand.raw_snippet,
            )
        )
    return scored


# --- Keyword-based fallback scorer (used when the LLM is unavailable) ---

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


def _fallback_score(cand: Candidate, intent: Intent) -> ScoredCandidate:
    """Keyword-based relevance score when the LLM scorer is unavailable."""
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

    is_target_role = "true" if is_hiring and not is_candidate_side else ("false" if is_candidate_side else "unknown")
    region_ok = ("true" if region_match else ("false" if region_match is False else "unknown"))

    return ScoredCandidate(
        id=cand.id, match=score,
        is_target_role=is_target_role, region_ok=region_ok,
        reason=f"fallback keyword score (hiring={is_hiring}, region={region_ok})",
        name=cand.name, headline=cand.headline, position=cand.position,
        company=cand.company, location_hint=cand.location_hint,
        linkedin_url=cand.linkedin_url,
        raw_title=cand.raw_title, raw_snippet=cand.raw_snippet,
    )


class Scorer:
    """Scores candidates in batches of 15-25, run concurrently."""

    def __init__(self, llm: LLM) -> None:
        self._llm = llm
        self._system = load("scorer.md")
        self._sem = asyncio.Semaphore(BATCH_CONCURRENCY)
        self._log = logger.bind(component="scorer")

    async def score(
        self,
        intent_json: str,
        candidates: list[Candidate],
        model: str | None = None,
        batch_size: int = BATCH_SIZE,
    ) -> list[ScoredCandidate]:
        model = model or settings.NVIDIA_MODEL_LARGE
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
                return await self._score_one_batch(intent_json, batch, model, intent)

        results = await asyncio.gather(*[_score_batch(b) for b in batches])
        merged: list[ScoredCandidate] = []
        for r in results:
            merged.extend(r)
        return merged

    async def _score_one_batch(
        self, intent_json: str, batch: list[Candidate], model: str, intent: Intent | None = None
    ) -> list[ScoredCandidate]:
        rendered = _render_candidates(batch)
        user = (
            f"Intent JSON:\n{intent_json}\n"
            f"Candidates JSON:\n{json.dumps(rendered, ensure_ascii=False)}\n"
        )

        try:
            data = await self._llm.json_call(
                model=model,
                system=self._system,
                user=user,
                temperature=settings.NVIDIA_TEMPERATURE_SCORER,
                response_format={"type": "json_object"},
            )
        except LLMError as exc:
            self._log.warning("scorer.batch_failed", n=len(batch), error=str(exc)[:200])
            return self._unknown_scores(batch, intent)

        # The model should return an array; json_object wraps it, so handle both.
        scores = data
        if isinstance(data, dict):
            for key in ("results", "scores", "candidates", "items"):
                if isinstance(data.get(key), list):
                    scores = data[key]
                    break
            else:
                scores = []

        if not isinstance(scores, list):
            self._log.warning("scorer.batch_not_list", n=len(batch))
            return self._unknown_scores(batch, intent)

        scored = _merge_scores(batch, scores)

        # Ensure every input id is present; re-ask for missing ones once.
        present = {s.id for s in scored}
        missing = [c for c in batch if c.id not in present]
        if missing:
            self._log.warning("scorer.missing_ids", missing_ids=[c.id for c in missing])
            repaired = await self._repair_missing(intent_json, missing, model, intent)
            scored.extend(repaired)

        return scored

    async def _repair_missing(
        self,
        intent_json: str,
        missing: list[Candidate],
        model: str,
        intent: Intent | None = None,
    ) -> list[ScoredCandidate]:
        rendered = _render_candidates(missing)
        user = (
            f"Intent JSON:\n{intent_json}\n"
            f"Score ONLY these candidates (return JSON array, one per id):\n"
            f"{json.dumps(rendered, ensure_ascii=False)}\n"
        )
        try:
            data = await self._llm.json_call(
                model=settings.NVIDIA_MODEL_SMALL,
                system=self._system,
                user=user,
                temperature=settings.NVIDIA_TEMPERATURE_SCORER,
                response_format={"type": "json_object"},
            )
        except LLMError as exc:
            self._log.warning("scorer.repair_failed", n=len(missing), error=str(exc)[:200])
            return self._unknown_scores(missing, intent)

        scores = data
        if isinstance(data, dict):
            for key in ("results", "scores", "candidates", "items"):
                if isinstance(data.get(key), list):
                    scores = data[key]
                    break
            else:
                scores = []
        return _merge_scores(missing, scores if isinstance(scores, list) else [])

    def _unknown_scores(
        self, candidates: list[Candidate], intent: Intent | None = None
    ) -> list[ScoredCandidate]:
        if intent is not None:
            return [_fallback_score(c, intent) for c in candidates]
        return [
            ScoredCandidate(
                id=c.id, match=0,
                is_target_role=RoleJudgment.UNKNOWN,
                region_ok=RegionJudgment.UNKNOWN, reason="scorer unavailable",
                name=c.name, headline=c.headline, position=c.position,
                company=c.company, location_hint=c.location_hint,
                linkedin_url=c.linkedin_url,
                raw_title=c.raw_title, raw_snippet=c.raw_snippet,
            )
            for c in candidates
        ]
