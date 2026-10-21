"""Unit tests for URL canonicalization and title parsing."""

from leadfinder.models import Candidate, SearchHit
from leadfinder.parser import Deduper, canonicalize_url, hit_to_candidate, parse_title


def test_canonicalize_url_strips_params_and_subdomain():
    assert canonicalize_url("https://in.linkedin.com/in/john-doe?trk=public_profile") == \
        "https://www.linkedin.com/in/john-doe"
    assert canonicalize_url("https://www.linkedin.com/in/john_doe/") == \
        "https://www.linkedin.com/in/john_doe"
    assert canonicalize_url("HTTP://LINKEDIN.COM/IN/Jane.Smith") == \
        "https://www.linkedin.com/in/jane.smith"


def test_canonicalize_rejects_non_profile():
    assert canonicalize_url("https://www.linkedin.com/company/acme") is None
    assert canonicalize_url("https://example.com") is None
    assert canonicalize_url("") is None


def test_parse_title_dash_format():
    out = parse_title("Riya Sharma - Technical Recruiter - Paytm | LinkedIn")
    assert out["name"] == "Riya Sharma"
    assert out["position"] == "Technical Recruiter"
    assert out["company"] == "Paytm"


def test_parse_title_at_format():
    out = parse_title("Arun Mehta - Senior Recruiter at Flipkart | LinkedIn")
    assert out["name"] == "Arun Mehta"
    assert out["position"] == "Senior Recruiter"
    assert out["company"] == "Flipkart"


def test_parse_title_name_only():
    out = parse_title("Sam Wilson | LinkedIn")
    assert out["name"] == "Sam Wilson"
    assert out["position"] == ""
    assert out["company"] == ""


def test_hit_to_candidate_filters_non_linkedin():
    hit = SearchHit(title="Some Article", snippet="x", url="https://example.com", position=1, source_query="q")
    assert hit_to_candidate(hit, "c0") is None


def test_deduper():
    d = Deduper()
    c1 = Candidate(id="a", name="A", linkedin_url="https://www.linkedin.com/in/alice")
    c2 = Candidate(id="b", name="B", linkedin_url="https://www.linkedin.com/in/ALICE")
    c3 = Candidate(id="c", name="C", linkedin_url="https://www.linkedin.com/in/bob")
    assert d.add(c1) is True
    assert d.add(c2) is False  # same slug, case-insensitive
    assert d.add(c3) is True
    assert len(d) == 2  # noqa: PLR2004
