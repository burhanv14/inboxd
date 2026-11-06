"""inboxd MCP server.

Exposes the lead discovery pipeline as Model Context Protocol tools so a user
can connect their own LLM client (opencode, claude, codex, antigravity, etc.)
to it. The NVIDIA LLM has been removed entirely; the server performs
deterministic planning, Google Custom Search, parsing, and keyword-based
scoring. The connected LLM is the "brain" — it decides what to search for and
how to interpret results.

Run:
    python agent.py mcp                    # start the MCP server over HTTP
    python agent.py mcp --port 8765
"""

from __future__ import annotations

import json
from pathlib import Path

from config import settings
from dork import build_dork
from models import Candidate, Intent, Lead, SearchHit
from output import write_csv, write_json
from parser import Deduper, parse_hits
from planner import _build_queries, _extract_intent
from scorer import Scorer
from search import GoogleCSEProvider

try:
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - surfaced at runtime
    raise ImportError(
        "fastmcp is required to run the MCP server. Install it with: pip install fastmcp"
    ) from exc

mcp = FastMCP("inboxd")


# --------------------------------------------------------------------------- tools


@mcp.tool()
async def discover_leads(
    request: str,
    max_results: int = 50,
    min_score: int = 7,
    pages: int = 2,
) -> str:
    """Run the full lead discovery pipeline from a natural-language request.

    Steps (all deterministic, no LLM):
      1. Extract intent from the request (rule-based).
      2. Build Google dork queries from the intent.
      3. Search via Google Custom Search API.
      4. Parse LinkedIn profile candidates from results.
      5. Score candidates with keyword heuristics.
      6. Filter by min_score and return the top max_results leads.

    This is a convenience wrapper around search_leads / parse_candidates /
    score_candidates / build_dork_query. The connected LLM client can call
    those lower-level tools directly for finer control.

    Args:
        request: Natural-language recruiting request, e.g.
            "Technical recruiters hiring Data Analysts in Bangalore".
        max_results: Maximum number of leads to return (default 50).
        min_score: Minimum keyword match score (0-10, default 7).
        pages: Search pages per dork query (default 2).

    Returns:
        JSON string with the list of leads and a run summary.
    """
    intent = _extract_intent(request)
    queries = _build_queries(intent)

    search = GoogleCSEProvider(concurrency=settings.SEARCH_CONCURRENCY)
    all_hits: list[SearchHit] = []
    try:
        for q in queries:
            for page in range(1, pages + 1):
                hits = await search.search(q.query, page=page, region=None)
                all_hits.extend(hits)
    finally:
        await search.close()

    # Parse to candidates.
    candidates, _ = parse_hits(all_hits, start_id=0)

    # Dedup.
    deduper = Deduper()
    unique: list[Candidate] = []
    for c in candidates:
        if deduper.add(c):
            unique.append(c)

    # Score (keyword-based).
    scorer = Scorer()
    intent_json = intent.model_dump_json()
    scored = await scorer.score(intent_json, unique)

    # Filter by min_score and build leads.
    leads: list[Lead] = []
    for s in scored:
        if s.match < min_score:
            continue
        leads.append(
            Lead(
                id=s.id, name=s.name, headline=s.headline,
                position=s.position, company=s.company,
                location_hint=s.location_hint, linkedin_url=s.linkedin_url,
                match=s.match, is_target_role=s.is_target_role,
                region_ok=s.region_ok, reason=s.reason,
                raw_title=s.raw_title, raw_snippet=s.raw_snippet,
            )
        )
    leads = leads[:max_results]

    payload = {
        "request": request,
        "intent": intent.model_dump(),
        "dorks": [q.query for q in queries],
        "count": len(leads),
        "leads": [lead.model_dump() for lead in leads],
        "summary": {
            "queries_run": len(queries),
            "search_hits": len(all_hits),
            "unique_candidates": len(unique),
            "matched": len(leads),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_leads(dork_query: str, page: int = 1) -> str:
    """Run a single Google Custom Search dork query.

    Returns raw search hits (title, snippet, url). Use this when the connected
    LLM wants to construct its own queries and page through results.

    Args:
        dork_query: A Google dork string, e.g.
            'site:linkedin.com/in ("Technical Recruiter") ("Bangalore")'.
        page: Search result page number (default 1).

    Returns:
        JSON string with the list of search hits.
    """
    search = GoogleCSEProvider(concurrency=settings.SEARCH_CONCURRENCY)
    try:
        hits = await search.search(dork_query, page=page, region=None)
    finally:
        await search.close()

    payload = {
        "query": dork_query,
        "page": page,
        "count": len(hits),
        "hits": [
            {
                "title": h.title,
                "snippet": h.snippet,
                "url": h.url,
                "position": h.position,
                "source_query": h.source_query,
            }
            for h in hits
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
async def parse_candidates(hits_json: str) -> str:
    """Parse Google search hits into LinkedIn profile candidates.

    Args:
        hits_json: JSON string with a list of hit objects, each having
            title, snippet, url, position, source_query.

    Returns:
        JSON string with the list of parsed candidates.
    """
    try:
        hits_data = json.loads(hits_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON: {exc}"})

    hits: list[SearchHit] = []
    for item in hits_data:
        hits.append(
            SearchHit(
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                url=item.get("url", ""),
                position=item.get("position", 0),
                source_query=item.get("source_query", ""),
            )
        )

    candidates, _ = parse_hits(hits, start_id=0)

    payload = {
        "count": len(candidates),
        "candidates": [
            {
                "id": c.id,
                "name": c.name,
                "headline": c.headline,
                "position": c.position,
                "company": c.company,
                "location_hint": c.location_hint,
                "linkedin_url": c.linkedin_url,
                "raw_title": c.raw_title,
                "raw_snippet": c.raw_snippet,
            }
            for c in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
async def score_candidates(candidates_json: str, intent_json: str) -> str:
    """Score candidates against an intent using keyword heuristics (no LLM).

    Args:
        candidates_json: JSON string with a list of candidate objects
            (id, name, headline, position, company, location_hint,
            linkedin_url, raw_title, raw_snippet).
        intent_json: JSON string with the intent (target_roles, job_title,
            company, region_terms, ...).

    Returns:
        JSON string with scored candidates.
    """
    try:
        candidates_data = json.loads(candidates_json)
        intent_data = json.loads(intent_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON: {exc}"})

    candidates: list[Candidate] = []
    for item in candidates_data:
        candidates.append(
            Candidate(
                id=item.get("id", ""),
                name=item.get("name", ""),
                headline=item.get("headline", ""),
                position=item.get("position", ""),
                company=item.get("company", ""),
                location_hint=item.get("location_hint", ""),
                linkedin_url=item.get("linkedin_url", ""),
                raw_title=item.get("raw_title", ""),
                raw_snippet=item.get("raw_snippet", ""),
            )
        )

    intent = Intent(**intent_data)

    scorer = Scorer()
    scored = await scorer.score(intent.model_dump_json(), candidates)

    payload = {
        "count": len(scored),
        "scored": [
            {
                "id": s.id,
                "match": s.match,
                "is_target_role": s.is_target_role,
                "region_ok": s.region_ok,
                "reason": s.reason,
                "name": s.name,
                "headline": s.headline,
                "position": s.position,
                "company": s.company,
                "location_hint": s.location_hint,
                "linkedin_url": s.linkedin_url,
                "raw_title": s.raw_title,
                "raw_snippet": s.raw_snippet,
            }
            for s in scored
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def build_dork_query(
    target_roles: list[str],
    job_title: str | None = None,
    region_terms: list[str] | None = None,
    company: str | None = None,
) -> str:
    """Build a Google dork query from intent terms (deterministic syntax).

    Every dork starts with site:linkedin.com/in. Multi-word terms are quoted,
    OR groups are parenthesized.

    Args:
        target_roles: Hiring-side roles, e.g. ["Technical Recruiter", "Talent Acquisition"].
        job_title: Specific job title being hired for, e.g. "Data Analyst".
        region_terms: Region/city terms, e.g. ["Bengaluru", "Bangalore"].
        company: Target company if named.

    Returns:
        A Google dork query string.
    """
    title_terms = [job_title] if job_title else None
    return build_dork(target_roles, title_terms, region_terms, company)


@mcp.tool()
def extract_intent(request: str) -> str:
    """Extract hiring intent from a natural-language request (rule-based, no LLM).

    Recognises hiring-side roles, region aliases, a job title ("hiring <title>"),
    and a company ("at <Company>").

    Args:
        request: Natural-language recruiting request.

    Returns:
        JSON string with the extracted intent.
    """
    intent = _extract_intent(request)
    return json.dumps(intent.model_dump(), ensure_ascii=False, indent=2)


@mcp.tool()
async def export_leads(leads_json: str, fmt: str = "csv", out: str | None = None) -> str:
    """Export leads to CSV or JSON.

    Args:
        leads_json: JSON string with a list of lead objects.
        fmt: Output format, "csv" or "json" (default csv).
        out: Output file path. If omitted, returns the content as a string.

    Returns:
        Status message or the file content.
    """
    try:
        leads_data = json.loads(leads_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON: {exc}"})

    leads: list[Lead] = []
    for item in leads_data:
        leads.append(
            Lead(
                id=item.get("id", ""),
                name=item.get("name", ""),
                headline=item.get("headline", ""),
                position=item.get("position", ""),
                company=item.get("company", ""),
                location_hint=item.get("location_hint", ""),
                linkedin_url=item.get("linkedin_url", ""),
                match=item.get("match", 0),
                is_target_role=item.get("is_target_role", "unknown"),
                region_ok=item.get("region_ok", "unknown"),
                reason=item.get("reason", ""),
                raw_title=item.get("raw_title", ""),
                raw_snippet=item.get("raw_snippet", ""),
            )
        )

    if fmt == "csv":
        path = Path(out) if out else Path("leads.csv")
        write_csv(leads, path)
        return f"Wrote {len(leads)} leads to {path}"
    if fmt == "json":
        path = Path(out) if out else Path("leads.json")
        write_json(leads, path, settings.PROMPT_VERSION)
        return f"Wrote {len(leads)} leads to {path}"
    return json.dumps({"error": f"unsupported format '{fmt}' (use csv or json)"})

