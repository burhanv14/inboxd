"""Lightweight terminal runner for inboxd.

Run the lead discovery agent directly with the system Python -- no virtualenv,
no `pip install -e`, no typer CLI boilerplate. Dependencies are expected to be
already importable (see pyproject.toml). `.env` is loaded automatically.

The LLM (NVIDIA NIM) has been removed. Lead discovery is exposed as an MCP
server; connect your own LLM client (opencode, claude, codex, antigravity,
etc.) to it.

On startup the terminal checks for required env vars (CSE_API_KEY, CSE_ID).
If missing, it prompts interactively and writes a .env file next to agent.py,
then starts the MCP server over HTTP (streamable-http). Press Ctrl+C to stop.

Usage:
    python agent.py                      # terminal: check env, start MCP over HTTP
    python agent.py run "..."            # one-shot lead discovery
    python agent.py run "..." --max-results 20 --out leads.csv --format csv
    python agent.py interactive          # interactive terminal (REPL)
    python agent.py mcp                  # start the MCP server over HTTP
    python agent.py mcp --port 8765      # custom port

"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from pathlib import Path

# Make `src/` importable without installing the package.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

load_dotenv()  # load .env if present (no hardcoded secrets)

from config import settings  # noqa: E402
from log import configure_logging, get_logger  # noqa: E402
from mcp_server import mcp  # noqa: E402
from orchestrator import Pipeline  # noqa: E402
from output import build_table, print_summary, write_csv, write_json  # noqa: E402
from planner import Planner  # noqa: E402
from scorer import Scorer  # noqa: E402
from search import GoogleCSEProvider  # noqa: E402
from rich.console import Console  # noqa: E402

logger = get_logger(__name__)
console = Console()


def build_pipeline(
    max_results: int,
    min_score: int,
    pages: int,
    search_concurrency: int,
) -> Pipeline:
    planner = Planner()
    search = GoogleCSEProvider(concurrency=search_concurrency)
    scorer = Scorer()
    return Pipeline(
        planner=planner,
        search=search,
        scorer=scorer,
        search_concurrency=search_concurrency,
        max_results=max_results,
        min_score=min_score,
        pages=pages,
    )


async def _run_and_close(pipeline: Pipeline, request: str) -> list:
    """Run the pipeline then close the search client on one event loop."""
    try:
        return await pipeline.run(request)
    finally:
        await pipeline.close()


def display(leads: list, pipeline: Pipeline, fmt: str, out: str | None) -> None:
    console.print(f"\n[bold green]{len(leads)} lead(s)[/bold green]")
    console.print(build_table(leads, settings.PROMPT_VERSION))
    print_summary(pipeline.summary())
    if out:
        fmt = fmt.lower()
        if fmt == "csv":
            write_csv(leads, Path(out))
        elif fmt == "json":
            write_json(leads, Path(out), settings.PROMPT_VERSION)
        else:
            console.print(f"[red]unsupported --format '{fmt}' (use csv or json)[/red]")
            raise SystemExit(2)


def _handle_error(exc: BaseException, verbose: bool, context: str) -> None:
    """Print a friendly error; full traceback only in verbose mode."""
    if verbose:
        traceback.print_exc()
    else:
        console.print(f"[red]{context} error:[/red] {exc}")


def run_once(args: argparse.Namespace) -> int:
    verbose = args.verbose or args.debug
    try:
        pipeline = build_pipeline(
            max_results=args.max_results,
            min_score=args.min_score,
            pages=args.pages,
            search_concurrency=args.search_concurrency,
        )
        try:
            leads = asyncio.run(_run_and_close(pipeline, args.request))
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/yellow]")
            leads = []
        except Exception as exc:
            _handle_error(exc, verbose, "run")
            leads = []
    except Exception as exc:
        _handle_error(exc, verbose, "setup")
        return 1
    display(leads, pipeline, args.format, args.out)
    return 0


def _read_request() -> str | None:
    """Read one line from stdin. Returns None on EOF/Interrupt."""
    try:
        return input()
    except (EOFError, KeyboardInterrupt):
        return None


def interactive(args: argparse.Namespace) -> int:
    verbose = args.verbose or args.debug
    console.print("[bold]inboxd interactive terminal[/bold]")
    console.print("Type a recruiting request, or 'exit'/'quit' to leave.\n")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    pipeline = None
    try:
        pipeline = build_pipeline(
            max_results=args.max_results,
            min_score=args.min_score,
            pages=args.pages,
            search_concurrency=args.search_concurrency,
        )
        while True:
            console.print("[bold cyan]inboxd>[/bold cyan] ", end="")
            sys.stdout.flush()
            request = _read_request()
            if request is None:
                console.print()
                break
            request = request.strip()
            if not request:
                continue
            if request.lower() in {"exit", "quit", "q"}:
                break

            try:
                leads = loop.run_until_complete(pipeline.run(request))
            except KeyboardInterrupt:
                console.print("\n[yellow]interrupted[/yellow]")
                leads = []
            except Exception as exc:
                _handle_error(exc, verbose, "request")
                leads = []

            if pipeline is not None:
                display(leads, pipeline, args.format, args.out)
            console.print()
    except Exception as exc:
        _handle_error(exc, verbose, "setup")
    finally:
        if pipeline is not None:
            try:
                loop.run_until_complete(pipeline.close())
            except Exception:  # noqa: S110 - shutdown must not raise
                pass
        try:
            loop.close()
        except Exception:  # noqa: S110 - loop close must not raise
            pass
    return 0


def _env_file_path() -> Path:
    """Return the .env file path next to agent.py."""
    return Path(__file__).resolve().parent / ".env"


def _read_existing_env() -> dict[str, str]:
    """Read existing .env file into a dict."""
    env_path = _env_file_path()
    existing: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            existing[key.strip()] = val.strip()
    return existing


def _write_env(values: dict[str, str]) -> None:
    """Merge values into .env next to agent.py."""
    env_path = _env_file_path()
    existing = _read_existing_env()
    existing.update(values)
    lines = [f"{k}={v}" for k, v in existing.items()]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Saved to {env_path}[/green]")


def ensure_env() -> dict[str, str]:
    """Ensure required env vars are present; prompt + write .env if missing.

    Returns the resolved values for the required keys.
    Works on macOS, Linux, and Windows.
    """
    required = ["CSE_API_KEY", "CSE_ID"]
    present = {k: os.environ.get(k, "") for k in required}
    missing = [k for k, v in present.items() if not v]

    if not missing:
        return present

    console.print("[bold yellow]Required environment variables are missing.[/bold yellow]")
    console.print("Please provide them below (they will be saved to .env).\n")
    values: dict[str, str] = {}
    for key in missing:
        while True:
            try:
                val = input(f"{key}: ").strip()
            except EOFError:
                console.print("[red]input required; cannot continue.[/red]")
                raise SystemExit(2)
            if val:
                values[key] = val
                break
            console.print(f"[red]{key} cannot be empty.[/red]")

    _write_env(values)
    for k, v in values.items():
        os.environ[k] = v

    # Reload the settings singleton so the new values take effect.
    import config as _cfg  # noqa: PLC0415

    _cfg.settings = _cfg.Settings()  # type: ignore[misc]
    return {**present, **values}


def run_mcp_terminal(port: int | None = None) -> int:
    """Start the MCP server over HTTP with graceful shutdown on terminal close.

    Works on macOS, Linux, and Windows. Press Ctrl+C to stop.
    """
    effective_port = port or settings.MCP_PORT
    console.print("[bold]Starting inboxd MCP server over HTTP[/bold]")
    console.print(f"  Host: {settings.MCP_HOST}  Port: {effective_port}")
    console.print("Press Ctrl+C to stop the server.\n")

    async def _run() -> None:
        await mcp.run_http_async(
            transport="streamable-http",
            host=settings.MCP_HOST,
            port=effective_port,
        )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down MCP server...[/yellow]")
    except asyncio.CancelledError:
        pass
    console.print("[green]MCP server stopped.[/green]")
    return 0


def mcp_server(args: argparse.Namespace) -> int:
    """Start the inboxd MCP server over HTTP (streamable-http) only."""
    return run_mcp_terminal(port=args.port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="inboxd terminal runner — recruiter/lead discovery via Google Custom Search.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- one-shot / interactive command (default) ---
    run_parser = subparsers.add_parser("run", help="Run a one-shot lead discovery request.")
    run_parser.add_argument(
        "request",
        help="Natural-language recruiting request.",
    )
    run_parser.add_argument("--max-results", type=int, default=settings.DEFAULT_MAX_RESULTS,
                            help="Max leads to return.")
    run_parser.add_argument("--min-score", type=int, default=settings.DEFAULT_MIN_SCORE,
                            help="Minimum match score (0-10).")
    run_parser.add_argument("--pages", type=int, default=settings.DEFAULT_PAGES,
                            help="Search pages per query.")
    run_parser.add_argument("--search-concurrency", type=int, default=settings.SEARCH_CONCURRENCY,
                            help="Search concurrency.")
    run_parser.add_argument("--format", default="table", help="table|csv|json.")
    run_parser.add_argument("--out", default=None, help="Write CSV/JSON to PATH.")
    run_parser.add_argument("--verbose", action="store_true", help="Debug logging.")
    run_parser.add_argument("--debug", action="store_true", help="Debug logging.")
    run_parser.set_defaults(func=run_once)

    # --- interactive command ---
    interactive_parser = subparsers.add_parser("interactive", help="Interactive terminal (REPL).")
    interactive_parser.add_argument("--max-results", type=int, default=settings.DEFAULT_MAX_RESULTS,
                                    help="Max leads to return.")
    interactive_parser.add_argument("--min-score", type=int, default=settings.DEFAULT_MIN_SCORE,
                                    help="Minimum match score (0-10).")
    interactive_parser.add_argument("--pages", type=int, default=settings.DEFAULT_PAGES,
                                    help="Search pages per query.")
    interactive_parser.add_argument("--search-concurrency", type=int, default=settings.SEARCH_CONCURRENCY,
                                    help="Search concurrency.")
    interactive_parser.add_argument("--format", default="table", help="table|csv|json.")
    interactive_parser.add_argument("--out", default=None, help="Write CSV/JSON to PATH.")
    interactive_parser.add_argument("--verbose", action="store_true", help="Debug logging.")
    interactive_parser.add_argument("--debug", action="store_true", help="Debug logging.")
    interactive_parser.set_defaults(func=interactive)

    # --- mcp server command ---
    mcp_parser = subparsers.add_parser("mcp", help="Start the inboxd MCP server (HTTP only).")
    mcp_parser.add_argument("--port", type=int, default=None,
                            help="Port for streamable-http transport.")
    mcp_parser.set_defaults(func=mcp_server)

    args = parser.parse_args(argv)

    # Ensure required env vars are present; prompt + write .env if missing.
    # Works on macOS, Linux, and Windows.
    ensure_env()

    debug = getattr(args, "verbose", False) or getattr(args, "debug", False)
    configure_logging(debug=debug, json_logs=not debug)

    problems = settings.validate_startup()
    if problems:
        for p in problems:
            console.print(f"[red]config error:[/red] {p}")
        return 2

    if args.command is None:
        # Default terminal flow: start the MCP server over HTTP.
        return run_mcp_terminal()

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
