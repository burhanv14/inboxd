"""Tests for scorer output schema and LLM JSON extraction/repair."""

import pytest
from pydantic import ValidationError

from leadfinder.llm import LLMError, _extract_json
from leadfinder.models import RoleJudgment, ScoredCandidate


def test_extract_json_strips_code_fences():
    text = '```json\n{"match": 7, "reason": "ok"}\n```'
    assert _extract_json(text) == {"match": 7, "reason": "ok"}


def test_extract_json_finds_balanced_braces():
    text = 'Here is the answer: {"id": "c1", "match": 5} end.'
    assert _extract_json(text) == {"id": "c1", "match": 5}


def test_extract_json_empty_raises():
    with pytest.raises(LLMError):
        _extract_json("")


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
