"""Tests for scorer output schema."""

import pytest
from pydantic import ValidationError

from models import RoleJudgment, ScoredCandidate


def test_scored_candidate_schema():
    s = ScoredCandidate(
        id="c1", match=8, is_target_role="true", region_ok="true", reason="strong match",
        name="Riya", headline="Recruiter", company="Paytm",
        linkedin_url="https://www.linkedin.com/in/riya",
    )
    assert s.match == 8  # noqa: PLR2004
    assert s.is_target_role == RoleJudgment.TRUE
    assert s.region_ok == "true"


def test_scored_candidate_match_bounds():
    ScoredCandidate(id="c1", match=0)
    ScoredCandidate(id="c1", match=10)
    with pytest.raises(ValidationError):
        ScoredCandidate(id="c1", match=11)
    with pytest.raises(ValidationError):
        ScoredCandidate(id="c1", match=-1)
