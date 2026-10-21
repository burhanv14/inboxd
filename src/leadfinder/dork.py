"""Dork builder and syntax validator.

The LLM supplies *terms*; this module controls *syntax*. Provides a deterministic
`build_dork()` helper plus validation/repair of raw dork strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from leadfinder.logging import get_logger

logger = get_logger(__name__)

MAX_DORK_LEN = 300


def _quote_term(term: str) -> str:
    """Quote a multi-word term; single words are left unquoted."""
    term = term.strip()
    if not term:
        return term
    if " " in term:
        return f'"{term}"'
    return term


def build_dork(
    role_terms: list[str],
    title_terms: list[str] | None = None,
    region_terms: list[str] | None = None,
    company: str | None = None,
) -> str:
    """Deterministically assemble a LinkedIn dork from term lists.

    Every dork starts with site:linkedin.com/in. Adjacent quoted groups are
    implicit AND; OR groups are parenthesized. Code controls syntax so the LLM
    only ever supplies terms.
    """
    parts: list[str] = ["site:linkedin.com/in"]

    if role_terms:
        parts.append("(" + " OR ".join(_quote_term(t) for t in role_terms) + ")")
    if title_terms:
        parts.append("(" + " OR ".join(_quote_term(t) for t in title_terms) + ")")
    if region_terms:
        parts.append("(" + " OR ".join(_quote_term(t) for t in region_terms) + ")")
    if company:
        parts.append(_quote_term(f"at {company}"))

    return " ".join(p for p in parts if p)


@dataclass
class DorkValidationResult:
    valid: bool
    dork: str
    reason: str = ""


def _balanced_parens(s: str) -> bool:
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _balanced_quotes(s: str) -> bool:
    return s.count('"') % 2 == 0


def _no_empty_groups(s: str) -> bool:
    return "()" not in s and '""' not in s


def _no_consecutive_operators(s: str) -> bool:
    # no AND AND, OR OR, OR AND, AND OR etc. (adjacent operator words)
    return not re.search(r"\b(AND|OR)\s+(AND|OR)\b", s, re.IGNORECASE)


def validate_dork(dork: str) -> DorkValidationResult:  # noqa: PLR0911 - multiple validation early-returns
    """Validate dork syntax. Returns a result with reason when invalid."""
    dork = dork.strip()
    if not dork:
        return DorkValidationResult(False, dork, "empty dork")
    if not dork.lower().startswith("site:linkedin.com/in"):
        return DorkValidationResult(False, dork, "must start with site:linkedin.com/in")
    if len(dork) > MAX_DORK_LEN:
        return DorkValidationResult(False, dork, f"exceeds {MAX_DORK_LEN} chars")
    if not _balanced_parens(dork):
        return DorkValidationResult(False, dork, "unbalanced parentheses")
    if not _balanced_quotes(dork):
        return DorkValidationResult(False, dork, "unbalanced quotes")
    if not _no_empty_groups(dork):
        return DorkValidationResult(False, dork, "empty group")
    if not _no_consecutive_operators(dork):
        return DorkValidationResult(False, dork, "consecutive operators")
    return DorkValidationResult(True, dork)


def repair_dork(dork: str) -> DorkValidationResult:
    """Attempt trivial auto-repairs; otherwise return the invalid result."""
    result = validate_dork(dork)
    if result.valid:
        return result
    fixed = dork

    # Fix unbalanced quotes by closing an open trailing quote
    if fixed.count('"') % 2 == 1:
        fixed = fixed.rstrip() + '"'

    # Fix unbalanced parens by closing open ones
    open_count = fixed.count("(")
    close_count = fixed.count(")")
    if open_count > close_count:
        fixed = fixed.rstrip() + ")" * (open_count - close_count)

    result = validate_dork(fixed)
    if result.valid:
        return DorkValidationResult(True, fixed, "auto-repaired")
    return DorkValidationResult(False, fixed, result.reason)


def dedup_queries(queries: list[str]) -> list[str]:
    """Remove duplicate queries while preserving order (case-insensitive)."""
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out
