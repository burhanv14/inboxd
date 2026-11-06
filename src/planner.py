"""Planner: NL request -> Intent -> QueryPlan (dorks), with adaptive re-plan.

The LLM has been removed. Intent is now extracted with a small rule-based
extractor, and dork queries are assembled deterministically by `dork.build_dork`.
The external LLM client (connected via MCP) can also supply its own intent terms
and call the lower-level tools directly.
"""

from __future__ import annotations

import re
from typing import Any

from dork import build_dork, repair_dork, validate_dork
from log import get_logger
from models import (
    DorkKind,
    Intent,
    PlannedQuery,
    QueryPlan,
)

logger = get_logger(__name__)

# --- Deterministic intent extraction (rule-based, no LLM) ---

_HIRING_ROLES = (
    "recruiter", "talent acquisition", "sourcer", "talent partner",
    "hr business partner", "hiring manager", "technical recruiter",
    "recruiting coordinator", "head of talent", "recruitment",
    "talent sourcer", "technical sourcer",
)

_REGION_MAP: dict[str, list[str]] = {
    "bangalore": ["Bengaluru", "Bangalore"],
    "bengaluru": ["Bengaluru", "Bangalore"],
    "new york": ["New York", "NYC"],
    "nyc": ["New York", "NYC"],
    "san francisco": ["San Francisco", "SF"],
    "sf": ["San Francisco", "SF"],
    "london": ["London"],
    "mumbai": ["Mumbai"],
    "delhi": ["Delhi", "New Delhi"],
    "new delhi": ["Delhi", "New Delhi"],
    "hyderabad": ["Hyderabad"],
    "pune": ["Pune"],
    "chennai": ["Chennai"],
    "singapore": ["Singapore"],
    "sydney": ["Sydney"],
    "toronto": ["Toronto"],
    "berlin": ["Berlin"],
    "austin": ["Austin"],
    "seattle": ["Seattle"],
    "chicago": ["Chicago"],
}


def _extract_intent(request: str) -> Intent:
    """Rule-based intent extraction from a natural-language request.

    Used when no LLM is available. Recognises hiring-side roles, region
    aliases, a job title ("hiring <title>"), and a company ("at <Company>").
    """
    lower = request.lower()

    target_roles = [r.title() for r in _HIRING_ROLES if r in lower]
    if not target_roles:
        target_roles = ["Recruiter"]

    region_terms: list[str] = []
    seen_regions: set[str] = set()
    for key, aliases in _REGION_MAP.items():
        if key in lower:
            for a in aliases:
                if a.lower() not in seen_regions:
                    seen_regions.add(a.lower())
                    region_terms.append(a)

    # Job title: look for "hiring <title>" / "for <title>" / "looking for <title>"
    job_title: str | None = None
    m = re.search(
        r"\b(?:hiring|for|looking for|seeking)\s+(.+?)(?:\s+(?:at|in|for|near)\b|\s*$)",
        request,
        re.IGNORECASE,
    )
    if m:
        candidate = m.group(1).strip().strip(".,;:")
        if candidate and candidate.lower() not in _HIRING_ROLES:
            job_title = candidate

    # Company: look for "at <Company>" (capitalized word(s))
    company: str | None = None
    m = re.search(r"\bat\s+([A-Z][A-Za-z0-9 &.'-]{2,40})\b", request)
    if m:
        company = m.group(1).strip()

    return Intent(
        target_roles=target_roles,
        job_title=job_title,
        company=company,
        region_terms=region_terms,
        notes="Rule-based intent extraction (no LLM).",
    )


def _build_queries(intent: Intent) -> list[PlannedQuery]:
    """Assemble deterministic dork queries from an Intent."""
    roles = intent.target_roles or ["Recruiter"]
    titles = [intent.job_title] if intent.job_title else []
    regions = intent.region_terms or []
    company = intent.company

    queries: list[PlannedQuery] = []
    queries.append(
        PlannedQuery(
            query=build_dork(roles, titles, regions, company),
            kind=DorkKind.STRICT,
            rationale="all role terms + job title + region + company",
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
            rationale="drop company, keep role + title + region",
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
    return unique_queries


class Planner:
    """Turns a natural-language request into an Intent and a QueryPlan.

    No LLM is used. Intent is extracted rule-based and dorks are built
    deterministically.
    """

    def __init__(self) -> None:
        self._log = logger.bind(component="planner")

    async def plan(
        self, request: str
    ) -> tuple[Intent, QueryPlan]:
        """Extract intent and generate the initial query plan (deterministic)."""
        intent = _extract_intent(request)
        queries = _build_queries(intent)
        plan = QueryPlan(queries=queries)

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
    ) -> QueryPlan:
        """Adaptive re-plan: propose relaxed/alternative queries deterministically.

        No LLM is used. Produces broad variants that differ from tried queries.
        """
        _ = request  # retained for signature compatibility
        _ = feedback  # retained for signature compatibility; heuristic replan ignores it
        tried = {q.query.lower() for q in previous.queries}

        # Build a relaxed plan: drop region and/or title, widen roles.
        roles = intent.target_roles or ["Recruiter"]
        broad_roles = list(dict.fromkeys(roles + (intent.synonyms or [])))
        candidate_queries: list[PlannedQuery] = []

        # Broad without region.
        candidate_queries.append(
            PlannedQuery(
                query=build_dork(broad_roles, [], []),
                kind=DorkKind.BROAD,
                rationale="replan: drop region and title",
            )
        )
        # Broad with region only.
        if intent.region_terms:
            candidate_queries.append(
                PlannedQuery(
                    query=build_dork(broad_roles, [], intent.region_terms),
                    kind=DorkKind.BROAD,
                    rationale="replan: region + roles only",
                )
            )
        # Title-only variant.
        if intent.job_title:
            candidate_queries.append(
                PlannedQuery(
                    query=build_dork(broad_roles, [intent.job_title], []),
                    kind=DorkKind.BROAD,
                    rationale="replan: title + roles only",
                )
            )

        plan = QueryPlan(queries=[])
        seen: set[str] = set(tried)
        for q in candidate_queries:
            if q.query.lower() in seen:
                continue
            res = validate_dork(q.query)
            if res.valid:
                seen.add(q.query.lower())
                plan.queries.append(q)
            else:
                repaired = repair_dork(q.query)
                if repaired.valid and repaired.dork.lower() not in seen:
                    seen.add(repaired.dork.lower())
                    plan.queries.append(
                        PlannedQuery(
                            query=repaired.dork,
                            kind=q.kind,
                            rationale=q.rationale,
                        )
                    )

        self._log.info("planner.replanned", n_queries=len(plan.queries))
        return plan

    def build_plan_from_terms(self, intent: Intent) -> QueryPlan:
        """Deterministic fallback plan assembled by code from intent terms."""
        return QueryPlan(queries=_build_queries(intent))

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
