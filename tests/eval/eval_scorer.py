"""Eval script for the scorer prompt.

Reads ``tests/eval/labeled_profiles.jsonl`` and reports precision / recall / F1
at the configured ``min_score`` for a deterministic reference rubric.

This gives a comparable baseline number. To evaluate a prompt change, swap the
``reference_score`` implementation for a call to the real LLM-based scorer and
re-run; compare the metrics.
"""

from __future__ import annotations

import json
from pathlib import Path

MIN_SCORE = 7

TARGET_ROLE_KEYWORDS = (
    "recruiter", "talent acquisition", "sourcer", "talent partner",
    "hr business partner", "hiring manager", "technical recruiter",
)
CANDIDATE_SIDE_KEYWORDS = (
    "data analyst", "software engineer", "product manager", "accountant",
    "open to work", "seeking", "looking for a job", "student",
)
NEGATIVE_KEYWORDS = (
    "ex-", "formerly", "ex ", "staffing agency", "rpo", "recruitment process outsourcing",
    "general staffing",
)


def reference_score(headline: str, snippet: str) -> int:
    """Deterministic reference rubric (stand-in for the LLM scorer)."""
    text = f"{headline} {snippet}".lower()
    score = 0
    if any(k in text for k in TARGET_ROLE_KEYWORDS):
        score += 6
    if any(k in text for k in CANDIDATE_SIDE_KEYWORDS):
        score -= 5
    if any(k in text for k in NEGATIVE_KEYWORDS):
        score -= 4
    return max(0, min(10, score))


def load_profiles(path: Path) -> list[dict]:
    profiles: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line:
                profiles.append(json.loads(line))
    return profiles


def main() -> None:
    profiles = load_profiles(Path("tests/eval/labeled_profiles.jsonl"))
    tp = fp = fn = tn = 0
    for p in profiles:
        expected = int(p["expected_match"])
        got = reference_score(p["headline"], p["snippet"])
        expected_pos = expected >= MIN_SCORE
        got_pos = got >= MIN_SCORE
        if expected_pos and got_pos:
            tp += 1
        elif got_pos and not expected_pos:
            fp += 1
        elif expected_pos and not got_pos:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    print(f"profiles: {len(profiles)}")
    print(f"min_score: {MIN_SCORE}")
    print(f"tp={tp} fp={fp} fn={fn} tn={tn}")
    print(f"precision={precision:.2f} recall={recall:.2f} f1={f1:.2f}")


if __name__ == "__main__":
    main()
