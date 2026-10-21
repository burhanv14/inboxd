# leadfinder

Async agent for recruiter/lead discovery. Given a natural-language request
(e.g. *"Technical recruiters hiring Data Analysts at fintech companies in
Bangalore"*), the agent:

1. Uses an LLM brain (NVIDIA NIM) to extract structured intent and build Google
   dork queries (`site:linkedin.com/in ...`).
2. Runs those queries through the **Google Custom Search API**, parses results
   into people (name, position, company, LinkedIn URL), dedups them.
3. Scores relevance with the LLM (strict rubric, hiring-side vs candidate-side).
4. Outputs a Rich table plus optional CSV/JSON.

Fully async (`asyncio`), no caching, graceful degradation, pydantic-validated
LLM outputs.

## Tech stack

- `typer` + `rich` (CLI, tables)
- `httpx` (AsyncClient, HTTP/2, pooled) for Google Custom Search
- `openai` SDK (`AsyncOpenAI`) → NVIDIA NIM
- `pydantic` v2 + `pydantic-settings`
- `tenacity` (retry), `aiolimiter` (rate limits)
- `structlog` (structured logging, per-stage timings)
- `pytest`, `pytest-asyncio`, `respx` (testing)

## Install

No virtualenv and no `pip install -e` is required. The agent runs directly
against the system Python as long as its dependencies are importable.

```bash
# 1. Configure
cp .env.example .env
# edit .env and set NVIDIA_API_KEY, CSE_API_KEY and CSE_ID

# 2. Run
python agent.py "Technical recruiters hiring Data Analysts in Bangalore"
```

## Usage

```bash
# one-shot run
python agent.py "Technical recruiters hiring Data Analysts in Bangalore"

# limit results and write CSV
python agent.py "..." --max-results 20 --out leads.csv --format csv

# interactive terminal (keep typing requests)
python agent.py
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--max-results` | 50 | Max leads to return |
| `--min-score` | 7 | Minimum match score (0-10) |
| `--pages` | 2 | Search pages per query |
| `--format` | table | table \| csv \| json |
| `--out` | — | Write CSV/JSON to PATH |
| `--verbose` / `--debug` | false | Debug logging |

## Configuration

See `.env.example`. Required: `NVIDIA_API_KEY`, `CSE_API_KEY`, `CSE_ID`.

## Project layout

```
leadfinder/
├── agent.py              # lightweight terminal runner (no venv / no pip install)
├── pyproject.toml
├── .env.example
├── README.md
├── DECISIONS.md
├── src/leadfinder/
│   ├── config.py         # pydantic-settings
│   ├── models.py         # pydantic v2 models
│   ├── llm.py            # AsyncOpenAI wrapper (json_call, retries, repair)
│   ├── planner.py        # NL -> Intent -> dork queries (+ adaptive re-plan)
│   ├── dork.py           # dork builder + syntax validator
│   ├── parser.py         # SearchHit -> Candidate + dedup
│   ├── scorer.py         # batched LLM relevance scoring
│   ├── search/           # SearchProvider ABC + GoogleCSEProvider
│   ├── orchestrator.py   # async queue pipeline
│   ├── output.py         # rich table, csv, json
│   ├── logging.py        # structlog
│   └── prompts/          # versioned markdown prompts
└── tests/
    ├── test_*.py
    └── eval/             # labeled profiles + eval script
```

## Legal / privacy notice

This tool automates discovery of publicly available professional contact info.
You are responsible for complying with applicable law (e.g. GDPR, the India
DPDP Act, CAN-SPAM) and the terms of service of LinkedIn, your search provider,
and NVIDIA NIM.

- Do not scrape google.com or LinkedIn pages directly; discovery uses search
  snippets only.

## Testing

```bash
pytest tests/            # unit + orchestrator tests (mocked, no network)
pytest tests/ -m live    # opt-in live smoke test (skipped by default)
python tests/eval/eval_scorer.py   # scorer baseline metrics
```

Lint: `ruff check src/ tests/`
