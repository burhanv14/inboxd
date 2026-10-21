# DECISIONS.md

Recorded design decisions and live-discovery findings for `leadfinder`.

## Build phases followed

scaffold → planner/dork → search (Google CSE) / parser → scorer → orchestrator /
output → terminal runner. Tests were run after each phase; no phase advanced while
tests failed.

## Key decisions

**No caching anywhere.** Only transient in-memory structures per run (a `set` for
URL dedup, queues, result lists). No SQLite/disk/TTL.

**Fully async.** `asyncio` throughout; no blocking calls in the event loop.

**No direct Google/LinkedIn scraping.** Discovery uses search title+snippet only
(via Google Custom Search). LinkedIn pages are never fetched.

**LLM outputs validated with pydantic.** One repair retry (small model), then
graceful skip. Invalid output never crashes a run.

**Graceful degradation.** Any stage failure yields partial results + a warning;
exit code 0 with partial results, non-zero only for config errors.

**Search provider abstract.** `SearchProvider.search(query, page, region)` with
`GoogleCSEProvider` as the Google Custom Search implementation.

**Dorks assembled by code, not the LLM.** `build_dork()` controls syntax
(parentheses, quotes, `site:linkedin.com/in` prefix). The LLM supplies term
lists; raw LLM dorks are only extra variants after validation.

**Adaptive re-plan.** Up to 2 extra rounds when unique candidates <
`--min-results`; never repeats earlier queries.

**Temperatures:** planner 0.2, scorer/parser/repair 0.0.

**Prompts versioned markdown** in `src/leadfinder/prompts/` with `PROMPT_VERSION`
logged every run.

## Orchestrator

Streaming pipeline `plan → search → parse/dedup → score → output` via bounded
`asyncio.Queue`s with backpressure. `--max-results` stops early and cancels
outstanding work. Per-stage timings and counts printed in the run summary.

## Config validation (startup)

Validates `NVIDIA_API_KEY`, `CSE_API_KEY`, and `CSE_ID`, with friendly errors.

## Tests

Unit tests cover dork building/validation, URL canonicalization, title parsing,
JSON extraction/repair, and a fully mocked end-to-end orchestrator run. One
opt-in live smoke test (`-m live`) is skipped by default.

## Verification of "no cache"

No DB/cache files are created by the code; only `.pytest_cache`, `.ruff_cache`
and `__pycache__` (tooling artifacts) may appear, all gitignored.
