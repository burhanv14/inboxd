"""Parse search hits into Candidates, canonicalize LinkedIn URLs, dedup."""

from __future__ import annotations

import re

from leadfinder.logging import get_logger
from leadfinder.models import Candidate, SearchHit

logger = get_logger(__name__)

# LinkedIn /in/ profile URL patterns
_PROFILE_RE = re.compile(
    r"https?://(?:[a-z]+\.)?linkedin\.com/in/([A-Za-z0-9_.-]+?)(?:/)?(?:\?.*)?$",
    re.IGNORECASE,
)

# Google title patterns:
#   Name - Title - Company | LinkedIn
#   Name - Title at Company | LinkedIn
#   Name | LinkedIn
_TITLE_DASH_RE = re.compile(r"^(?P<name>.+?)\s*-\s*(?P<title>.+?)\s*-\s*(?P<company>.+?)\s*\| LinkedIn\s*$", re.IGNORECASE)
_TITLE_AT_RE = re.compile(r"^(?P<name>.+?)\s*-\s*(?P<title>.+?)\s+at\s+(?P<company>.+?)\s*\| LinkedIn\s*$", re.IGNORECASE)
_TITLE_ONLY_RE = re.compile(r"^(?P<name>.+?)\s*\| LinkedIn\s*$", re.IGNORECASE)

# Location hint extraction from snippet
_LOCATION_RE = re.compile(
    r"\b(in|at|based in|located in)\s+([A-Za-z][A-Za-z .&'-]{2,40})",
    re.IGNORECASE,
)


def canonicalize_url(url: str) -> str | None:
    """Force https://www.linkedin.com/in/<slug>, strip params/trailing slash/country subdomain.

    Returns None when the URL is not a LinkedIn /in/ profile.
    """
    m = _PROFILE_RE.search(url.strip())
    if not m:
        return None
    slug = m.group(1).lower().rstrip("/")
    # Drop trailing dots / hyphens artifacts
    slug = slug.strip(".-")
    if not slug:
        return None
    return f"https://www.linkedin.com/in/{slug}"


def parse_title(title: str) -> dict[str, str]:
    """Parse a Google result title into name / position / company using regex.

    Returns dict with keys name, position, company (may be empty).
    """
    title = title.strip()
    m = _TITLE_DASH_RE.match(title)
    if m:
        return {
            "name": m.group("name").strip(),
            "position": m.group("title").strip(),
            "company": m.group("company").strip(),
        }
    m = _TITLE_AT_RE.match(title)
    if m:
        return {
            "name": m.group("name").strip(),
            "position": m.group("title").strip(),
            "company": m.group("company").strip(),
        }
    m = _TITLE_ONLY_RE.match(title)
    if m:
        return {"name": m.group("name").strip(), "position": "", "company": ""}
    # Fallback: treat whole thing as name
    return {"name": title, "position": "", "company": ""}


def extract_location_hint(snippet: str) -> str:
    """Extract a location hint from a search snippet when present."""
    m = _LOCATION_RE.search(snippet or "")
    if m:
        return m.group(0).strip()
    return ""


def hit_to_candidate(hit: SearchHit, cid: str) -> Candidate | None:
    """Convert a SearchHit to a Candidate; returns None if not a LinkedIn profile."""
    url = canonicalize_url(hit.url)
    if not url:
        return None
    parsed = parse_title(hit.title)
    return Candidate(
        id=cid,
        name=parsed["name"],
        headline=parsed["position"],
        position=parsed["position"],
        company=parsed["company"],
        location_hint=extract_location_hint(hit.snippet),
        linkedin_url=url,
        raw_title=hit.title,
        raw_snippet=hit.snippet,
    )


def parse_hits(hits: list[SearchHit], start_id: int = 0) -> tuple[list[Candidate], int]:
    """Parse a list of SearchHits into Candidates, returning (candidates, next_id)."""
    candidates: list[Candidate] = []
    nid = start_id
    for hit in hits:
        if not hit.is_linkedin_profile:
            continue
        cand = hit_to_candidate(hit, f"c{nid}")
        if cand:
            candidates.append(cand)
            nid += 1
    return candidates, nid


class Deduper:
    """In-memory dedup on canonical LinkedIn slug for a single run."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def add(self, candidate: Candidate) -> bool:
        """Return True if the candidate is new (added), False if duplicate."""
        slug = candidate.linkedin_url.rstrip("/").rsplit("/in/", 1)[-1].lower()
        if slug in self._seen:
            return False
        self._seen.add(slug)
        return True

    def __len__(self) -> int:
        return len(self._seen)


def filter_linkedin_profiles(hits: list[SearchHit]) -> list[SearchHit]:
    """Keep only hits whose URL is a LinkedIn /in/ profile."""
    return [h for h in hits if h.is_linkedin_profile]
