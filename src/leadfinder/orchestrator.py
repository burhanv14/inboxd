"""Streaming async queue pipeline: plan → search → parse/dedup → score → output.

Stages overlap via bounded asyncio.Queues with backpressure. Clean shutdown with
sentinel values; try/finally; no orphaned tasks. --max-results stops early and
cancels outstanding work. Per-stage timings and counts printed in the run summary.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from leadfinder.llm import LLM
from leadfinder.logging import get_logger
from leadfinder.models import (
    Candidate,
    Intent,
    Lead,
    PlannedQuery,
    ScoredCandidate,
    SearchHit,
)
from leadfinder.parser import Deduper, parse_hits
from leadfinder.planner import Planner
from leadfinder.scorer import Scorer
from leadfinder.search.base import SearchProvider

logger = get_logger(__name__)

_SENTINEL = object()

# Batch size for the scorer stage.
_SCORE_BATCH_SIZE = 20


@dataclass
class StageMetrics:
    name: str
    started: float = 0.0
    ended: float = 0.0
    items_in: int = 0
    items_out: int = 0

    @property
    def duration_s(self) -> float:
        return round((self.ended or time.monotonic()) - (self.started or 0.0), 3)


@dataclass
class RunMetrics:
    queries_run: int = 0
    search_hits: int = 0
    unique_candidates: int = 0
    matched: int = 0
    stages: dict[str, StageMetrics] = field(default_factory=dict)

    def stage(self, name: str) -> StageMetrics:
        return self.stages.setdefault(name, StageMetrics(name=name))


class Pipeline:
    def __init__(  # noqa: PLR0913, PLR0917 - pipeline wiring needs many collaborators/limits
        self,
        planner: Planner,
        llm: LLM,
        search: SearchProvider,
        scorer: Scorer,
        search_concurrency: int = 8,
        llm_concurrency: int = 8,
        queue_size: int = 200,
        max_results: int = 50,
        min_score: int = 7,
        min_results: int = 10,
        pages: int = 2,
        second_pass: bool = False,
    ) -> None:
        self._planner = planner
        self._llm = llm
        self._search = search
        self._scorer = scorer
        self._search_concurrency = search_concurrency
        self._llm_concurrency = llm_concurrency
        self._queue_size = queue_size
        self._max_results = max_results
        self._min_score = min_score
        self._min_results = min_results
        self._pages = pages
        self._second_pass = second_pass
        self._metrics = RunMetrics()
        self._stop = asyncio.Event()
        self._log = logger.bind(component="orchestrator")

    # ------------------------------------------------------------------ run

    async def run(self, request: str) -> list[Lead]:
        leads: list[Lead] = []
        leads_lock = asyncio.Lock()

        # Reset LLM availability for each run (scorer is reused across requests).
        if hasattr(self._scorer, "llm_available"):
            self._scorer.llm_available = True

        intent_holder: dict[str, Any] = {}
        queues = self._create_queues()

        stage_tasks = [
            asyncio.create_task(self._plan_stage(request, queues["plan"], intent_holder)),
            asyncio.create_task(self._search_stage(queues["plan"], queues["parse"])),
            asyncio.create_task(self._parse_stage(queues["parse"], queues["score"])),
            asyncio.create_task(self._score_stage(intent_holder, queues["score"], leads, leads_lock)),
        ]

        # Run stages concurrently. If any stage fails, cancel the rest and
        # degrade gracefully (return whatever leads were collected so far).
        try:
            await asyncio.gather(*stage_tasks)
        except Exception as exc:
            self._log.error("pipeline.stage_failed", error=str(exc)[:200])
            self._stop.set()
            for task in stage_tasks:
                task.cancel()
            # Await cancellations so no pending tasks are destroyed.
            await asyncio.gather(*stage_tasks, return_exceptions=True)

        return leads

    # ------------------------------------------------------------------ queues

    @staticmethod
    def _create_queues() -> dict[str, asyncio.Queue]:
        return {
            "plan": asyncio.Queue(maxsize=1),
            "parse": asyncio.Queue(maxsize=200),
            "score": asyncio.Queue(maxsize=200),
        }

    # ------------------------------------------------------------------ stages

    async def _plan_stage(
        self, request: str, plan_q: asyncio.Queue, intent_holder: dict[str, Any]
    ) -> None:
        m = self._metrics.stage("plan")
        m.started = time.monotonic()
        intent, plan = await self._planner.plan(request)
        intent_holder["intent"] = intent
        await plan_q.put(plan)
        await plan_q.put(_SENTINEL)
        m.ended = time.monotonic()
        m.items_out = len(plan.queries)
        self._log.info("plan.done", n_queries=len(plan.queries))

    async def _search_stage(self, plan_q: asyncio.Queue, parse_q: asyncio.Queue) -> None:
        m = self._metrics.stage("search")
        m.started = time.monotonic()
        sem = asyncio.Semaphore(self._search_concurrency)

        async def _search_query(q: PlannedQuery) -> None:
            async with sem:
                for page in range(1, self._pages + 1):
                    if self._stop.is_set():
                        return
                    try:
                        hits = await self._search.search(q.query, page=page, region=None)
                    except Exception as exc:
                        self._log.warning(
                            "search.query_error", query=q.query[:60], error=str(exc)[:120]
                        )
                        continue
                    self._metrics.queries_run += 1
                    self._metrics.search_hits += len(hits)
                    for h in hits:
                        await parse_q.put(h)

        while not self._stop.is_set():
            try:
                item = await asyncio.wait_for(plan_q.get(), timeout=0.5)
            except TimeoutError:
                continue
            if item is _SENTINEL:
                await parse_q.put(_SENTINEL)
                return
            plan = item
            await asyncio.gather(*[_search_query(q) for q in plan.queries])
        m.ended = time.monotonic()

    async def _parse_stage(self, parse_q: asyncio.Queue, score_q: asyncio.Queue) -> None:
        m = self._metrics.stage("parse")
        m.started = time.monotonic()
        deduper = Deduper()
        next_id = 0
        while not self._stop.is_set():
            try:
                hit = await asyncio.wait_for(parse_q.get(), timeout=0.5)
            except TimeoutError:
                continue
            if hit is _SENTINEL:
                await score_q.put(_SENTINEL)
                return
            cands, next_id = parse_hits([hit], start_id=next_id)
            for c in cands:
                m.items_in += 1
                self._metrics.unique_candidates += 1
                if deduper.add(c):
                    m.items_out += 1
                    await score_q.put(c)

    async def _score_stage(
        self,
        intent_holder: dict[str, Any],
        score_q: asyncio.Queue,
        leads: list[Lead],
        leads_lock: asyncio.Lock,
    ) -> None:
        m = self._metrics.stage("score")
        m.started = time.monotonic()
        intent = intent_holder.get("intent")
        intent_json = intent.model_dump_json() if intent else "{}"
        batch: list[Candidate] = []

        async def _flush() -> None:
            nonlocal batch
            if not batch:
                return
            to_score = batch[:]
            batch = []
            try:
                scored = await self._scorer.score(intent_json, to_score)
            except Exception as exc:
                if hasattr(self._scorer, "llm_available"):
                    self._scorer.llm_available = False
                self._log.warning("score.batch_error", error=str(exc)[:160])
                scored = [self._unknown(c) for c in to_score]
            for s in scored:
                m.items_out += 1
                # When the LLM scorer is unavailable, include all candidates
                # regardless of --min-score so the user still sees results.
                if getattr(self._scorer, "llm_available", True) and s.match < self._min_score:
                    continue
                lead = self._build_lead(s)
                async with leads_lock:
                    leads.append(lead)
                    self._metrics.matched += 1
                    if len(leads) >= self._max_results:
                        self._stop.set()

        while not self._stop.is_set():
            try:
                item = await asyncio.wait_for(score_q.get(), timeout=0.5)
            except TimeoutError:
                if batch:
                    await _flush()
                continue
            if item is _SENTINEL:
                await _flush()
                return
            cand: Candidate = item
            m.items_in += 1
            batch.append(cand)
            if len(batch) >= _SCORE_BATCH_SIZE:
                await _flush()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _unknown(c: Candidate) -> ScoredCandidate:
        return ScoredCandidate(
            id=c.id, match=0,
            is_target_role="unknown", region_ok="unknown", reason="scorer unavailable",
            name=c.name, headline=c.headline, position=c.position, company=c.company,
            location_hint=c.location_hint, linkedin_url=c.linkedin_url,
            raw_title=c.raw_title, raw_snippet=c.raw_snippet,
        )

    def _build_lead(self, scored: ScoredCandidate) -> Lead:
        return Lead(
            id=scored.id, name=scored.name, headline=scored.headline,
            position=scored.position, company=scored.company,
            location_hint=scored.location_hint, linkedin_url=scored.linkedin_url,
            match=scored.match, is_target_role=scored.is_target_role,
            region_ok=scored.region_ok, reason=scored.reason,
            raw_title=scored.raw_title, raw_snippet=scored.raw_snippet,
        )

    async def close(self) -> None:
        """Close the underlying search HTTP client."""
        try:
            await self._search.close()
        except Exception:  # noqa: S110 - shutdown cleanup must not raise
            pass

    def summary(self) -> dict[str, Any]:
        return {
            "queries_run": self._metrics.queries_run,
            "search_hits": self._metrics.search_hits,
            "unique_candidates": self._metrics.unique_candidates,
            "matched": self._metrics.matched,
            "stages": {
                name: {
                    "duration_s": m.duration_s,
                    "items_in": m.items_in,
                    "items_out": m.items_out,
                }
                for name, m in self._metrics.stages.items()
            },
        }
