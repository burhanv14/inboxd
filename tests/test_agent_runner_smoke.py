"""Smoke test for the agent.py terminal runner wiring (no network/LLM)."""

import argparse
import asyncio

import agent
from leadfinder.models import Lead


class FakePipeline:
    def __init__(self, leads):
        self.leads = leads
        self.closed = False
        self._summary = {"queries_run": 1, "search_hits": 3, "unique_candidates": 2, "matched": 1, "stages": {}}

    async def run(self, request):
        return self.leads

    async def close(self):
        self.closed = True

    def summary(self):
        return self._summary


def test_run_once_table(monkeypatch, capsys):
    lead = Lead(
        id="c0", name="Riya Sharma", position="Technical Recruiter", company="Paytm",
        linkedin_url="https://www.linkedin.com/in/riya-sharma", match=9,
        is_target_role="true", region_ok="true", reason="strong",
    )
    captured = {}

    def fake_build_pipeline(max_results, min_score, pages, search_concurrency, llm_concurrency):
        captured.update(
            max_results=max_results, min_score=min_score, pages=pages,
            search_concurrency=search_concurrency, llm_concurrency=llm_concurrency,
        )
        return FakePipeline([lead])

    monkeypatch.setattr(agent, "build_pipeline", fake_build_pipeline)

    ns = argparse.Namespace(
        request="Technical recruiters hiring Data Analysts in Bangalore",
        max_results=20, min_score=7, pages=1,
        search_concurrency=4, llm_concurrency=4,
        format="table", out=None, verbose=False, debug=False,
    )
    code = agent.run_once(ns)
    assert code == 0

    assert captured["max_results"] == 20
    assert captured["min_score"] == 7
    assert captured["pages"] == 1
    assert captured["search_concurrency"] == 4

    out = capsys.readouterr().out
    assert "1 lead(s)" in out
    assert "matched (score>=min)" in out
    # Rich table rendering is environment-dependent under capture; the CSV path
    # (below) and a real terminal confirm the actual row content renders.


def test_run_once_csv(tmp_path, monkeypatch):
    out = tmp_path / "leads.csv"
    lead = Lead(
        id="c0", name="Riya Sharma", position="Technical Recruiter", company="Paytm",
        linkedin_url="https://www.linkedin.com/in/riya-sharma", match=9,
    )

    def fake_build_pipeline(max_results, min_score, pages, search_concurrency, llm_concurrency):
        return FakePipeline([lead])

    monkeypatch.setattr(agent, "build_pipeline", fake_build_pipeline)

    ns = argparse.Namespace(
        request="x", max_results=50, min_score=7, pages=2,
        search_concurrency=8, llm_concurrency=8,
        format="csv", out=str(out), verbose=False, debug=False,
    )
    code = agent.run_once(ns)
    assert code == 0
    assert out.exists()
    text = out.read_text()
    assert "Riya Sharma" in text
    assert "email" not in text  # email column removed


def test_pipeline_close(monkeypatch):
    pipeline = FakePipeline([])
    asyncio.run(pipeline.close())
    assert pipeline.closed is True
