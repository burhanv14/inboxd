"""Smoke test for the agent.py interactive terminal loop (no network/LLM)."""

import argparse

import agent
from leadfinder.models import Lead


class FakePipeline:
    def __init__(self, leads):
        self.leads = leads
        self.closed = False
        self._summary = {
            "queries_run": 1, "search_hits": 3, "unique_candidates": 2,
            "matched": 1, "stages": {},
        }

    async def run(self, request):
        return self.leads

    async def close(self):
        self.closed = True

    def summary(self):
        return self._summary


def test_interactive_exit_immediately(monkeypatch, capsys):
    """Typing 'exit' should end the session without processing any request."""
    calls = []

    def fake_build_pipeline(**kwargs):
        calls.append(kwargs)
        return FakePipeline([])

    monkeypatch.setattr(agent, "build_pipeline", fake_build_pipeline)
    # Feed 'exit' as the first line.
    monkeypatch.setattr("builtins.input", lambda prompt="": "exit")

    ns = argparse.Namespace(
        request=None, max_results=50, min_score=7, pages=2,
        search_concurrency=8, llm_concurrency=8,
        format="table", out=None, verbose=False, debug=False,
    )
    code = agent.interactive(ns)
    assert code == 0
    assert calls == []  # no pipeline built for an immediate exit


def test_interactive_processes_one_request_then_eof(monkeypatch, capsys):
    """One valid request is processed; EOF ends the session."""
    lead = Lead(id="c0", name="Riya Sharma", position="Recruiter", company="Paytm",
                linkedin_url="https://www.linkedin.com/in/riya-sharma", match=9)

    pipeline_holder = {}

    def fake_build_pipeline(**kwargs):
        p = FakePipeline([lead])
        pipeline_holder["p"] = p
        return p

    monkeypatch.setattr(agent, "build_pipeline", fake_build_pipeline)
    # First line: a real request; second line: EOF (input returns None -> break).
    inputs = iter(["Technical recruiters in Bangalore", None])

    def fake_input(prompt=""):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)

    ns = argparse.Namespace(
        request=None, max_results=50, min_score=7, pages=2,
        search_concurrency=8, llm_concurrency=8,
        format="table", out=None, verbose=False, debug=False,
    )
    code = agent.interactive(ns)
    assert code == 0
    assert pipeline_holder["p"].closed is True

    out = capsys.readouterr().out
    assert "1 lead(s)" in out or "Riya Sharma" in out or "matched" in out
