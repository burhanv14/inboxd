# inboxd

Recruiter/lead discovery agent powered by Google Custom Search, exposed as a **Model Context Protocol (MCP) server**.

Give it a plain-English hiring request (e.g. *"Technical recruiters hiring Data Analysts in Bangalore"*) and it returns scored LinkedIn leads — complete with name, headline, company, location, match score, and reason.

> **How it works:** inboxd does the deterministic work (intent extraction → Google dorks → search → LinkedIn parsing → keyword scoring → export). The LLM is *your* AI coding tool, which you connect to inboxd over MCP.

---

## Contents

- [What it does](#what-it-does)
- [Prerequisites](#prerequisites)
- [1. Install the library](#1-install-the-library)
- [2. Configure your API keys](#2-configure-your-api-keys)
- [3. Start the MCP server in a terminal](#3-start-the-mcp-server-in-a-terminal)
- [4. Connect your AI coding tool](#4-connect-your-ai-coding-tool)
- [MCP tools reference](#mcp-tools-reference)
- [Example workflow](#example-workflow)
- [Configuration reference](#configuration-reference)
- [Tips & troubleshooting](#tips--troubleshooting)

---

## What it does

| Step | What happens |
|------|--------------|
| Extract intent | Turns a natural-language request into hiring roles, job title, region, company |
| Build dorks | Builds Google site:linkedin.com/in search queries |
| Search | Queries Google Custom Search API |
| Parse | Pulls LinkedIn profile candidates from results |
| Score | Keyword-heuristics scoring against your intent (0–10) |
| Export | Returns matched leads as JSON/CSV |

All logic is deterministic — there is no embedded LLM. You bring your own LLM via MCP.

---

## Prerequisites

- **Python 3.11+**
- A **Google Custom Search API key** (`CSE_API_KEY`) and a **Search Engine ID** (`CSE_ID`)
  - Get them from the [Google Cloud Console](https://console.cloud.google.com/) → Custom Search API

---

## 1. Install the library

Clone or unzip the repo, then install:

```bash
cd /path/to/inboxd
pip install -e .
```

> No install is strictly required — `agent.py` adds `src/` to its own path — but `pip install -e .` makes the `inboxd` package importable everywhere.

---

## 2. Configure your API keys

Create a `.env` next to `agent.py`:

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

```
CSE_API_KEY=your_google_custom_search_api_key
CSE_ID=your_search_engine_id
```

Optional (defaults shown):

```
MCP_HOST=127.0.0.1
MCP_PORT=8765
SEARCH_CONCURRENCY=8
DEFAULT_MAX_RESULTS=50
DEFAULT_MIN_SCORE=7
DEFAULT_PAGES=2
```

> Never commit `.env`. It's already in `.gitignore`.

---

## 3. Start the MCP server in a terminal

Open a terminal in the repo folder and run:

```bash
# macOS / Linux
./run_agent.sh mcp

# Windows
run_agent.bat mcp

# or directly with python
python agent.py mcp
```

Expected output:

```
Starting inboxd MCP server over HTTP
  Host: 127.0.0.1  Port: 8765
Press Ctrl+C to stop the server.
```

Leave this terminal **running** in the background. The MCP server listens at:

```
http://127.0.0.1:8765/mcp
```

Use a custom port if needed:

```bash
python agent.py mcp --port 8765
```

Press `Ctrl+C` to stop.

> On first run, if `CSE_API_KEY`/`CSE_ID` are missing, the terminal prompts you for them and saves them to `.env` automatically.

---

## 4. Connect your AI coding tool

The MCP server must be running (step 3) before you connect.  
**Server URL:** `http://127.0.0.1:8765/mcp`

Below are example configurations for popular AI coding tools. Exact file locations and key names vary by tool version — the one constant is the server URL above.

### OpenCode

OpenCode already ships an `opencode.json` in this repo pointing at the local server. To add it to your user settings:

**File:** `~/.opencode/mcp.json` (or via OpenCode settings UI)

```json
{
  "mcpServers": {
    "inboxd": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp",
      "enabled": true
    }
  }
}
```

### Claude Code (Anthropic)

**File:** `~/.claude.json`

```json
{
  "mcpServers": {
    "inboxd": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Then restart Claude Code. You can also add it with:

```bash
claude mcp add inboxd --url http://127.0.0.1:8765/mcp
```

### OpenAI Codex CLI

**File:** `~/.codex/config.toml`

```toml
[[mcp_servers]]
name = "inboxd"
type = "http"
url = "http://127.0.0.1:8765/mcp"
```

### Cursor

**File:** `~/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "inboxd": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Open Cursor settings → Features → MCP and verify `inboxd` is connected.

### antigravity

Add a remote MCP server pointing at the local endpoint:

```json
{
  "servers": {
    "inboxd": {
      "transport": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

### Any other MCP client

Use these connection details:

- **Transport:** streamable-HTTP
- **URL:** `http://127.0.0.1:8765/mcp`
- **Auth:** none (local-only)

Once connected, ask your AI assistant to use the inboxd tools (see reference below), e.g.:

> "Find technical recruiters in Bangalore hiring data analysts and export the top 10 to CSV."

---

## MCP tools reference

inboxd exposes these tools to your connected AI client:

| Tool | What it does | Example args |
|------|--------------|--------------|
| `discover_leads` | Full pipeline: intent → search → parse → score → return leads | `request`, `max_results`, `min_score`, `pages` |
| `search_leads` | Run a single Google dork query; returns raw hits | `dork_query`, `page` |
| `parse_candidates` | Parse Google hits into LinkedIn candidates | `hits_json` |
| `score_candidates` | Score candidates against an intent (keyword heuristics) | `candidates_json`, `intent_json` |
| `build_dork_query` | Build a Google dork from intent terms | `target_roles`, `job_title`, `region_terms`, `company` |
| `extract_intent` | Extract hiring intent from a natural-language request | `request` |
| `export_leads` | Export leads to CSV or JSON | `leads_json`, `fmt`, `out` |

Use `discover_leads` for the simplest experience; the lower-level tools give your AI client finer control.

---

## Example workflow

1. Start the server: `python agent.py mcp` (leave it running)
2. Connect your AI coding tool to `http://127.0.0.1:8765/mcp`
3. Ask your assistant:

   > "Find technical recruiters and talent acquisition leads hiring Data Engineers in Bengaluru or Hyderabad. Score them, keep only matches ≥ 7, and export to `leads.csv`."

4. The assistant calls `discover_leads` (or the chain of lower-level tools) and returns scored LinkedIn leads.

---

## Configuration reference

| Variable | Purpose | Default |
|----------|---------|---------|
| `CSE_API_KEY` | Google Custom Search API key *(required)* | — |
| `CSE_ID` | Google Custom Search Engine ID *(required)* | — |
| `MCP_HOST` | MCP server bind host | `127.0.0.1` |
| `MCP_PORT` | MCP server port | `8765` |
| `SEARCH_CONCURRENCY` | Parallel search requests | `8` |
| `DEFAULT_MAX_RESULTS` | Max leads per run | `50` |
| `DEFAULT_MIN_SCORE` | Minimum match score (0–10) | `7` |
| `DEFAULT_PAGES` | Search pages per dork | `2` |
| `LOG_LEVEL` | Logging level | `INFO` |

---

## Tips & troubleshooting

| Problem | Fix |
|---------|-----|
| `config error: CSE_API_KEY is not set` | Fill `CSE_API_KEY` and `CSE_ID` in `.env`, then restart the server |
| MCP client can't connect | Make sure `python agent.py mcp` is running and the URL/port match your client config |
| Port already in use | Use a different port: `python agent.py mcp --port 8766` |
| No leads returned | Lower `--min-score`, raise `--max-results` or `--pages`, or broaden your request |
| Windows: `python` not found | Try `py agent.py mcp` or install Python from python.org |

Keep the MCP terminal open while you work — closing it disconnects your AI coding tool.

---

*inboxd — local, deterministic lead discovery. Bring your own LLM.*