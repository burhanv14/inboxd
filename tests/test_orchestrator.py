"""End-to-end orchestrator test with fully mocked planner/search/scorer."""

import asyncio

from leadfinder.models import (
    Intent,
    PlannedQuery,
    QueryPlan,
    ScoredCandidate,
    SearchHit,
)
from leadfinder.orchestrator import Pipeline


class FakePlanner:
    def __init__(self, queries):
        self.queries = queries

    async def plan(self, request):
        intent = Intent(
            target_roles=["Recruiter"], job_title="Data Analyst",
            region_terms=["Bangalore"],
        )
        plan = QueryPlan(queries=[PlannedQuery(query=q, kind="strict", rationale="t") for q in self.queries])
        return intent, plan


class FakeSearch:
    def __init__(self, hits):
        self.hits = hits
        self.closed = False
        self.calls = 0

    async def search(self, query, page, region=None):
        self.calls += 1
        return self.hits

    async def close(self):
        self.closed = True


class FakeScorer:
    def __init__(self, scored):
        self.scored = scored

    async def score(self, intent_json, candidates):
        return self.scored


def _make_hit(slug, name, position, company):
    return SearchHit(
        title=f"{name} - {position} - {company} | LinkedIn",
        snippet="hiring data analysts",
        url=f"https://www.linkedin.com/in/{slug}",
        position=1, source_query="q",
    )


def test_orchestrator_end_to_end_filters_by_score():
    hits = [
        _make_hit("riya-sharma", "Riya Sharma", "Technical Recruiter", "Paytm"),
        _make_hit("arun-mehta", "Arun Mehta", "Data Analyst", "Flipkart"),
    ]
    scored = [
        ScoredCandidate(
            id="c0", match=9, is_target_role="true", region_ok="true", reason="strong",
            name="Riya Sharma", headline="Technical Recruiter", position="Technical Recruiter",
            company="Paytm", linkedin_url="https://www.linkedin.com/in/riya-sharma",
            raw_title=hits[0].title, raw_snippet=hits[0].snippet,
        ),
        ScoredCandidate(
            id="c1", match=2, is_target_role="false", region_ok="true", reason="wrong role",
            name="Arun Mehta", headline="Data Analyst", position="Data Analyst",
            company="Flipkart", linkedin_url="https://www.linkedin.com/in/arun-mehta",
            raw_title=hits[1].title, raw_snippet=hits[1].snippet,
        ),
    ]

    async def _run():
        planner = FakePlanner(["site:linkedin.com/in (Recruiter)"])
        search = FakeSearch(hits)
        scorer = FakeScorer(scored)
        pipe = Pipeline(
            planner=planner, llm=None, search=search, scorer=scorer,
            max_results=50, min_score=7, pages=1,
            second_pass=False, search_concurrency=4, llm_concurrency=4,
        )
        leads = await pipe.run("Technical recruiters hiring Data Analysts in Bangalore")
        await search.close()
        return leads, search, pipe

    leads, search, pipe = asyncio.run(_run())
    assert search.closed is True
    assert search.calls >= 1
    # Only Riya (score 9 >= 7) should appear; Arun (score 2) filtered out.
    assert len(leads) == 1
    assert leads[0].name == "Riya Sharma"
    summary = pipe.summary()
    assert summary["queries_run"] >= 1
    assert summary["unique_candidates"] == 2  # noqa: PLR2004
    assert summary["matched"] == 1
