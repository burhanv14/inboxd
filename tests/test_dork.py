"""Unit tests for dork building and validation."""

from leadfinder.dork import build_dork, dedup_queries, repair_dork, validate_dork


def test_build_dork_strict():
    dork = build_dork(
        role_terms=["Technical Recruiter", "Talent Acquisition"],
        title_terms=["Data Analyst"],
        region_terms=["Bengaluru", "Bangalore"],
        company=None,
    )
    assert dork.startswith("site:linkedin.com/in")
    assert '("Technical Recruiter" OR "Talent Acquisition")' in dork
    assert '("Data Analyst")' in dork
    assert "(Bengaluru OR Bangalore)" in dork
    assert validate_dork(dork).valid


def test_build_dork_with_company():
    dork = build_dork(["Recruiter"], ["Data Analyst"], ["Bangalore"], "Paytm")
    assert 'at "Paytm"' in dork or "at Paytm" in dork
    assert validate_dork(dork).valid


def test_single_word_term_not_quoted():
    dork = build_dork(["Recruiter"], None, None, None)
    assert '"Recruiter"' not in dork
    assert "Recruiter" in dork


def test_validate_balanced_parens():
    assert validate_dork('site:linkedin.com/in ("A" OR "B") ("C"))').valid is False


def test_validate_must_start_with_site():
    assert validate_dork('linkedin.com/in ("Recruiter")').valid is False


def test_validate_empty_group():
    assert validate_dork('site:linkedin.com/in () ("Recruiter")').valid is False


def test_validate_consecutive_operators():
    assert validate_dork('site:linkedin.com/in ("Recruiter") OR AND ("Hiring")').valid is False


def test_repair_unbalanced_quote():
    res = repair_dork('site:linkedin.com/in ("Recruiter')
    assert res.valid


def test_dedup_queries_preserves_order():
    qs = ["a", "b", "A", "c", "b"]
    assert dedup_queries(qs) == ["a", "b", "c"]
