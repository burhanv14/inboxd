"""Planner: NL request -> Intent -> QueryPlan (dorks), with adaptive re-plan."""

from __future__ import annotations

from typing import Any

from leadfinder.config import settings
from leadfinder.dork import build_dork, repair_dork, validate_dork
from leadfinder.llm import LLM
from leadfinder.logging import get_logger
from leadfinder.models import (
    DorkKind,
    Intent,
    PlannedQuery,
    QueryPlan,
)
from leadfinder.prompts import load

logger = get_logger(__name__)


class Planner:
    """Turns a natural-language request into an Intent and a QueryPlan."""

    def __init__(self, llm: LLM) -> None:
        self._llm = llm
        self._log = logger.bind(component="planner")
        self._system = load("planner.md")

    async def plan(
        self, request: str, model: str | None = None
    ) -> tuple[Intent, QueryPlan]:
        """Extract intent and generate the initial query plan."""
        model = model or settings.NVIDIA_MODEL_LARGE
        user = f'Request: "{request}"\nReturn JSON with keys: intent, plan.'
        try:
            data = await self._llm.json_call(
                model=model,
                system=self._system,
                user=user,
                temperature=settings.NVIDIA_TEMPERATURE_PLANNER,
                response_format={"type": "json_object"},
            )
            intent = Intent(**data["intent"])
            plan = QueryPlan(**data["plan"])
        except Exception as exc:
            self._log.warning("planner.llm_failed", error=str(exc)[:200])
            intent = self._fallback_intent(request)
            plan = self._fallback_plan(intent)

        # Normalize: ensure every dork is valid; auto-repair or drop invalid ones.
        plan.queries = self._sanitize_queries(plan)
        self._log.info(
            "planner.completed",
            roles=intent.target_roles,
            job_title=intent.job_title,
            region=intent.region_terms,
            n_queries=len(plan.queries),
        )
        return intent, plan

    async def replan(
        self,
        request: str,
        intent: Intent,
        previous: QueryPlan,
        feedback: list[dict[str, Any]],
        model: str | None = None,
    ) -> QueryPlan:
        """Adaptive re-plan: ask the LLM for relaxed/alternative queries given feedback.

        feedback: list of {query, hits_count} plus top rejected reasons.
        Never repeats earlier queries.
        """
        model = model or settings.NVIDIA_MODEL_LARGE
        tried = [q.query for q in previous.queries]
        tried_str = "\n".join(f"- {q}" for q in tried)
        fb_str = "\n".join(f"- {f.get('query')}: {f.get('hits_count')} hits" for f in feedback)
        system = (
            "You are a sourcing analyst. The previous search returned too few candidates. "
            "Propose NEW, relaxed Google dorks (site:linkedin.com/in only) that differ from "
            "the tried queries. Return JSON: {plan: {queries: [{query, kind, rationale}]}}. "
            "Do not repeat tried queries."
        )
        user = (
            f"Request: {request}\n"
            f"Intent: {intent.model_dump_json()}\n"
            f"Tried queries:\n{tried_str}\n"
            f"Feedback (query: hits):\n{fb_str}\n"
        )
        try:
            data = await self._llm.json_call(
                model=model,
                system=system,
                user=user,
                temperature=settings.NVIDIA_TEMPERATURE_PLANNER,
                response_format={"type": "json_object"},
            )
            plan = QueryPlan(**data["plan"])
        except Exception as exc:
            self._log.warning("planner.replan_failed", error=str(exc)[:200])
            plan = QueryPlan(queries=[])

        plan.queries = self._sanitize_queries(plan)
        # Drop anything already tried.
        existing = {q.query.lower() for q in previous.queries}
        plan.queries = [q for q in plan.queries if q.query.lower() not in existing]
        self._log.info("planner.replanned", n_queries=len(plan.queries))
        return plan

    def build_plan_from_terms(self, intent: Intent) -> QueryPlan:
        """Deterministic fallback plan assembled by code from intent terms."""
        roles = intent.target_roles or ["Recruiter"]
        titles = [intent.job_title] if intent.job_title else []
        regions = intent.region_terms or []
        company = intent.company

        queries: list[PlannedQuery] = []
        queries.append(
            PlannedQuery(
                query=build_dork(roles, titles, regions, company),
                kind=DorkKind.STRICT,
                rationale="all role terms + job title + region",
            )
        )
        broad_roles = list(dict.fromkeys(roles + (intent.synonyms or [])))
        queries.append(
            PlannedQuery(
                query=build_dork(broad_roles, titles, [], company),
                kind=DorkKind.BROAD,
                rationale="drop region, add role synonyms",
            )
        )
        queries.append(
            PlannedQuery(
                query=build_dork(roles, titles, regions),
                kind=DorkKind.SYNONYM,
                rationale="industry keyword variant",
            )
        )
        queries.append(
            PlannedQuery(
                query=build_dork(broad_roles, [], regions),
                kind=DorkKind.BROAD,
                rationale="drop job title, keep region + role synonyms",
            )
        )
        # Dedup query strings while preserving PlannedQuery objects.
        seen: set[str] = set()
        unique_queries: list[PlannedQuery] = []
        for q in queries:
            if q.query.lower() not in seen:
                seen.add(q.query.lower())
                unique_queries.append(q)
        return QueryPlan(queries=unique_queries)

    # --- internals ---

    def _sanitize_queries(self, plan: QueryPlan) -> list[PlannedQuery]:
        out: list[PlannedQuery] = []
        seen: set[str] = set()
        for q in plan.queries:
            res = validate_dork(q.query)
            if res.valid:
                if q.query.lower() not in seen:
                    seen.add(q.query.lower())
                    out.append(q)
            else:
                repaired = repair_dork(q.query)
                if repaired.valid and repaired.dork.lower() not in seen:
                    seen.add(repaired.dork.lower())
                    out.append(
                        PlannedQuery(
                            query=repaired.dork,
                            kind=q.kind,
                            rationale=q.rationale + (" (auto-repaired)" if repaired.reason else ""),
                        )
                    )
                else:
                    self._log.warning(
                        "planner.dropped_invalid_dork",
                        query=q.query,
                        reason=res.reason,
                    )
        return out

    def _fallback_intent(self, request: str) -> Intent:
        return Intent(
            target_roles=["Recruiter", "Talent Acquisition"],
            notes=f"LLM intent extraction failed; raw request: {request[:200]}",
        )

    def _fallback_plan(self, intent: Intent) -> QueryPlan:
        return self.build_plan_from_terms(intent)


def hits_feedback(
    query_hits: list[tuple[str, int]],
) -> list[dict[str, Any]]:
    """Build feedback entries from (query, hits_count) pairs for re-planning."""
    return [{"query": q, "hits_count": n} for q, n in query_hits]
