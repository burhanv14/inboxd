"""Pydantic v2 data models for the leadfinder pipeline."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Intent(BaseModel):
    """Structured intent extracted from a natural-language request."""

    target_roles: list[str] = Field(
        default_factory=list,
        description="Hiring-side roles the user wants, e.g. ['Recruiter', 'Talent Acquisition'].",
    )
    job_title: str | None = Field(
        default=None,
        description="Specific job title being recruited for, e.g. 'Data Analyst'.",
    )
    company: str | None = Field(
        default=None,
        description="Target company if named in the request.",
    )
    industry: str | None = Field(
        default=None,
        description="Target industry if named, e.g. 'fintech'.",
    )
    region_terms: list[str] = Field(
        default_factory=list,
        description="Region/city terms including aliases, e.g. ['Bengaluru', 'Bangalore'].",
    )
    seniority: str | None = Field(
        default=None,
        description="Requested seniority if mentioned.",
    )
    synonyms: list[str] = Field(
        default_factory=list,
        description="Role synonyms discovered during planning.",
    )
    notes: str | None = Field(
        default=None,
        description="Free-form observations from the intent extraction.",
    )


class DorkKind(str, Enum):  # noqa: UP042 - pydantic v2 needs str Enums
    STRICT = "strict"
    BROAD = "broad"
    COMPANY = "company"
    SYNONYM = "synonym"


class PlannedQuery(BaseModel):
    """A single search dork plus metadata about why it was planned."""

    query: str = Field(..., min_length=1)
    kind: DorkKind
    rationale: str = ""

    @field_validator("query")
    @classmethod
    def _not_just_site(cls, v: str) -> str:
        if v.strip().lower() == "site:linkedin.com/in":
            raise ValueError("query must contain terms beyond site:linkedin.com/in")
        return v


class QueryPlan(BaseModel):
    """The full set of planned search dorks."""

    queries: list[PlannedQuery] = Field(default_factory=list)


class SearchHit(BaseModel):
    """A single Google Custom Search result row (title + snippet only)."""

    title: str
    snippet: str = ""
    url: str
    position: int = 0
    source_query: str = ""

    @property
    def is_linkedin_profile(self) -> bool:
        """True when the URL looks like a LinkedIn /in/ profile page."""
        return "/in/" in self.url and "linkedin.com" in self.url


class Candidate(BaseModel):
    """A parsed LinkedIn profile candidate (pre-scoring)."""

    id: str
    name: str
    headline: str = ""
    position: str = ""
    company: str = ""
    location_hint: str = ""
    linkedin_url: str
    raw_title: str = ""
    raw_snippet: str = ""

    @field_validator("linkedin_url")
    @classmethod
    def _non_empty_url(cls, v: str) -> str:
        if not v or "linkedin.com/in/" not in v:
            raise ValueError("linkedin_url must be a LinkedIn /in/ profile URL")
        return v


class RoleJudgment(str, Enum):  # noqa: UP042 - pydantic v2 needs str Enums
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class RegionJudgment(str, Enum):  # noqa: UP042 - pydantic v2 needs str Enums
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class ScoredCandidate(BaseModel):
    """A candidate plus LLM relevance scores."""

    id: str
    match: int = Field(ge=0, le=10)
    is_target_role: RoleJudgment = RoleJudgment.UNKNOWN
    region_ok: RegionJudgment = RegionJudgment.UNKNOWN
    reason: str = ""

    # populated by the orchestrator after enrichment
    name: str = ""
    headline: str = ""
    position: str = ""
    company: str = ""
    location_hint: str = ""
    linkedin_url: str = ""
    raw_title: str = ""
    raw_snippet: str = ""


class Lead(BaseModel):
    """Final output row: a scored candidate."""

    id: str
    name: str = ""
    headline: str = ""
    position: str = ""
    company: str = ""
    location_hint: str = ""
    linkedin_url: str = ""
    match: int = 0
    is_target_role: RoleJudgment = RoleJudgment.UNKNOWN
    region_ok: RegionJudgment = RegionJudgment.UNKNOWN
    reason: str = ""
    raw_title: str = ""
    raw_snippet: str = ""

    def to_csv_row(self) -> dict[str, str | int | None]:
        return {
            "id": self.id,
            "name": self.name,
            "position": self.position,
            "company": self.company,
            "linkedin_url": self.linkedin_url,
            "match": self.match,
            "reason": self.reason,
        }
