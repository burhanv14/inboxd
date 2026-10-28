"""Lightweight terminal runner for leadfinder.

Run the lead discovery agent directly with the system Python -- no virtualenv,
no `pip install -e`, no typer CLI boilerplate. Dependencies are expected to be
already importable (see pyproject.toml). `.env` is loaded automatically.

Usage:
    python agent.py "Technical recruiters hiring Data Analysts in Bangalore"
    python agent.py "..." --max-results 20 --out leads.csv --format csv
    python agent.py                      # interactive terminal (REPL)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from pathlib import Path

# Make `src/leadfinder` importable without installing the package.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

load_dotenv()  # load .env if present (no hardcoded secrets)

from leadfinder.config import settings  # noqa: E402
from leadfinder.llm import LLM  # noqa: E402
from leadfinder.logging import configure_logging, get_logger  # noqa: E402
from leadfinder.orchestrator import Pipeline  # noqa: E402
from leadfinder.output import build_table, print_summary, write_csv, write_json  # noqa: E402
from leadfinder.planner import Planner  # noqa: E402
from leadfinder.scorer import Scorer  # noqa: E402
from leadfinder.search import GoogleCSEProvider  # noqa: E402
from rich.console import Console  # noqa: E402

logger = get_logger(__name__)
console = Console()


def build_pipeline(
    max_results: int,
    min_score: int,
    pages: int,
    search_concurrency: int,
    llm_concurrency: int,
    no_llm: bool = False,
) -> Pipeline:
    llm = LLM(requests_per_second=float(llm_concurrency))
    planner = Planner(llm)
    search = GoogleCSEProvider(concurrency=search_concurrency)
    scorer = Scorer(llm, no_llm=no_llm)
    return Pipeline(
        planner=planner,
        llm=llm,
        search=search,
        scorer=scorer,
        search_concurrency=search_concurrency,
        llm_concurrency=llm_concurrency,
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
            llm_concurrency=args.llm_concurrency,
            no_llm=args.no_llm,
        )
        try:
            leads = asyncio.run(_run_and_close(pipeline, args.request))
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/yellow]")
            leads = []
        except Exception as exc:  # noqa: BLE001 - keep one-shot alive-ish; report cleanly
            _handle_error(exc, verbose, "run")
            leads = []
    except Exception as exc:  # noqa: BLE001 - pipeline build failure must not crash silently
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
    console.print("[bold]leadfinder interactive terminal[/bold]")
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
            llm_concurrency=args.llm_concurrency,
            no_llm=args.no_llm,
        )
        while True:
            console.print("[bold cyan]leadfinder>[/bold cyan] ", end="")
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
            except Exception as exc:  # noqa: BLE001 - one bad request won't kill the session
                _handle_error(exc, verbose, "request")
                leads = []

            if pipeline is not None:
                display(leads, pipeline, args.format, args.out)
            console.print()
    except Exception as exc:  # noqa: BLE001 - pipeline build failure won't kill the session
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="leadfinder terminal runner — recruiter/lead discovery via Google Custom Search.",
    )
    parser.add_argument(
        "request",
        nargs="?",
        help="Natural-language recruiting request (omitted => interactive terminal).",
    )
    parser.add_argument("--max-results", type=int, default=settings.DEFAULT_MAX_RESULTS,
                        help="Max leads to return.")
    parser.add_argument("--min-score", type=int, default=settings.DEFAULT_MIN_SCORE,
                        help="Minimum match score (0-10).")
    parser.add_argument("--pages", type=int, default=settings.DEFAULT_PAGES,
                        help="Search pages per query.")
    parser.add_argument("--search-concurrency", type=int, default=settings.SEARCH_CONCURRENCY,
                        help="Search concurrency.")
    parser.add_argument("--llm-concurrency", type=int, default=settings.LLM_CONCURRENCY,
                        help="LLM concurrency.")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip the LLM scorer entirely (instant keyword fallback).")
    parser.add_argument("--format", default="table", help="table|csv|json.")
    parser.add_argument("--out", default=None, help="Write CSV/JSON to PATH.")
    parser.add_argument("--verbose", action="store_true", help="Debug logging.")
    parser.add_argument("--debug", action="store_true", help="Debug logging.")
    args = parser.parse_args(argv)

    debug = args.verbose or args.debug
    configure_logging(debug=debug, json_logs=not debug)

    problems = settings.validate_startup()
    if problems:
        for p in problems:
            console.print(f"[red]config error:[/red] {p}")
        return 2

    if not args.request:
        return interactive(args)
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
