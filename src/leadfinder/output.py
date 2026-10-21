"""Output writers: Rich table, CSV, JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from leadfinder.logging import get_logger
from leadfinder.models import Lead

logger = get_logger(__name__)


def build_table(leads: list[Lead], prompt_version: str) -> Table:
    """Build a Rich table of leads."""
    table = Table(title=f"leadfinder results (prompt v{prompt_version})", show_lines=False)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Position", style="green")
    table.add_column("Company")
    table.add_column("LinkedIn URL", style="blue", no_wrap=True)
    table.add_column("Score", justify="right", style="yellow")

    for i, lead in enumerate(leads, 1):
        table.add_row(
            str(i),
            lead.name,
            lead.position,
            lead.company,
            lead.linkedin_url,
            str(lead.match),
        )
    return table


def print_summary(metrics: dict[str, Any]) -> None:
    """Print the run summary as a Rich table."""
    console = Console()
    table = Table(title="Run summary", show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    rows = [
        ("queries_run", metrics.get("queries_run", 0)),
        ("search_hits", metrics.get("search_hits", 0)),
        ("unique_candidates", metrics.get("unique_candidates", 0)),
        ("matched (score>=min)", metrics.get("matched", 0)),
    ]
    for k, v in rows:
        table.add_row(k, str(v))
    console.print(table)

    stages = metrics.get("stages", {})
    if stages:
        st = Table(title="Stage timings", show_lines=False)
        st.add_column("Stage", style="bold")
        st.add_column("Duration (s)", justify="right")
        st.add_column("In", justify="right")
        st.add_column("Out", justify="right")
        for name, m in stages.items():
            st.add_row(name, str(m.get("duration_s", 0)), str(m.get("items_in", 0)), str(m.get("items_out", 0)))
        console.print(st)


def write_csv(leads: list[Lead], path: Path) -> None:
    """Write leads to a CSV file."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id", "name", "position", "company", "linkedin_url", "match", "reason",
            ],
        )
        writer.writeheader()
        for lead in leads:
            writer.writerow(lead.to_csv_row())
    logger.info("output.csv_written", path=str(path), n=len(leads))


def write_json(leads: list[Lead], path: Path, prompt_version: str) -> None:
    """Write leads to a JSON file."""
    payload = {
        "prompt_version": prompt_version,
        "count": len(leads),
        "leads": [lead.model_dump() for lead in leads],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("output.json_written", path=str(path), n=len(leads))
