"""The prose is written after the analysis is already usable, inside a time budget.

Groq's free tier allows ~8K tokens a minute and the client waits out rate limits, so unbounded narration
turned a 10 s analysis into a 6 minute one. These tests pin the behaviour that prevents that.
"""
import json
import shutil
import time
from pathlib import Path

import pytest

from parser.pipeline import generate_narratives as gn
from parser.pipeline import ingest_repo
from parser.pipeline.generate_narratives import NarrationBudgetExceeded, call_groq

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


class FakeResponse:
    def __init__(self, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def ok(text="hello"):
    return FakeResponse(200, body={"choices": [{"message": {"content": text}}]})


# ------------------------------------------------------------------ the Groq client

def test_a_rate_limit_longer_than_the_time_left_raises_instead_of_sleeping(monkeypatch):
    slept = []
    monkeypatch.setattr(gn.time, "sleep", slept.append)
    monkeypatch.setattr(gn.requests, "post", lambda *a, **k: FakeResponse(429, {"retry-after": "30"}))
    with pytest.raises(NarrationBudgetExceeded):
        call_groq("p", "key", "model", deadline=time.monotonic() + 5)
    assert slept == []  # the old behaviour: sleep up to 60 s, five times


def test_an_expired_deadline_sends_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(gn.requests, "post", lambda *a, **k: calls.append(1) or ok())
    with pytest.raises(NarrationBudgetExceeded):
        call_groq("p", "key", "model", deadline=time.monotonic() - 1)
    assert calls == []


def test_without_a_deadline_it_still_waits_out_a_rate_limit(monkeypatch):
    """Local runs and the demo build have no budget and rely on the patient retry."""
    slept = []
    monkeypatch.setattr(gn.time, "sleep", slept.append)
    replies = iter([FakeResponse(429, {"retry-after": "7"}), ok("done")])
    monkeypatch.setattr(gn.requests, "post", lambda *a, **k: next(replies))
    assert call_groq("p", "key", "model") == "done"
    assert slept == [7.0]


def test_a_rate_limit_that_fits_inside_the_budget_is_waited_out(monkeypatch):
    slept = []
    monkeypatch.setattr(gn.time, "sleep", slept.append)
    replies = iter([FakeResponse(429, {"retry-after": "3"}), ok("done")])
    monkeypatch.setattr(gn.requests, "post", lambda *a, **k: next(replies))
    assert call_groq("p", "key", "model", deadline=time.monotonic() + 60) == "done"
    assert slept == [3.0]


# ------------------------------------------------------------------ the pipeline

PHASE_ONE_LLM_CALLS: list = []


@pytest.fixture
def work(tmp_path, monkeypatch):
    """A work dir produced by phase one on the sample repo, with Groq configured but forbidden."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("REARCH_CLUSTERING", "graph")
    monkeypatch.setattr(ingest_repo, "clone_or_update", lambda url, dest: shutil.copytree(FIXTURE, dest))
    monkeypatch.setattr(ingest_repo, "current_commit_sha", lambda repo_dir: "a" * 40)

    # Guard the network boundary itself, and record rather than only raise: migration_plan.main deliberately
    # swallows LLM errors, so an exception alone would let a phase-one request slip through unnoticed. (Phase one
    # does call call_groq, with an already-expired deadline; that raises before sending anything.)
    PHASE_ONE_LLM_CALLS.clear()

    def forbidden(*args, **kwargs):
        PHASE_ONE_LLM_CALLS.append(1)
        raise AssertionError("phase one must not send a request to the LLM")

    monkeypatch.setattr(gn.requests, "post", forbidden)
    directory = tmp_path / "work"
    ingest_repo.main("https://github.com/o/r", str(directory), narrate=False)
    return directory


def test_phase_one_is_complete_without_any_llm_call(work):
    assert PHASE_ONE_LLM_CALLS == []
    plan = json.loads((work / "migration.json").read_text(encoding="utf-8"))
    assert plan["summary"]["subsystems"] > 0
    assert plan["narrative"] is None
    assert json.loads((work / "narratives.json").read_text(encoding="utf-8")) == {}


def test_phase_one_does_not_leave_the_previous_runs_narratives_behind(tmp_path, monkeypatch, work):
    """narratives.json is numbered for one clustering; keeping it after re-clustering would label the wrong subsystems."""
    (work / "narratives.json").write_text(json.dumps({"0": {"text": "STALE", "member_count": 1, "grounded_in": {}}}), encoding="utf-8")
    shutil.rmtree(work / "repo")
    ingest_repo.main("https://github.com/o/r", str(work), narrate=False)
    assert json.loads((work / "narratives.json").read_text(encoding="utf-8")) == {}


def fake_llm(calls, fail_notes_after=None):
    """Plan prompts mention only subsystem numbers; note prompts are recognised by their system prompt."""
    notes = {"count": 0}

    def fake(prompt, api_key, model, system=gn.SYSTEM_PROMPT, max_tokens=220, deadline=None):
        is_plan = system != gn.SYSTEM_PROMPT
        calls.append("plan" if is_plan else "note")
        if is_plan:
            return "## Target shape\nExtract `#0` first."
        notes["count"] += 1
        if fail_notes_after is not None and notes["count"] > fail_notes_after:
            raise NarrationBudgetExceeded("out of time")
        return "This subsystem handles one concern."

    return fake


def test_the_written_plan_comes_first_then_notes_and_purposes_are_attached(work, monkeypatch):
    calls = []
    monkeypatch.setattr(gn, "call_groq", fake_llm(calls))
    state = ingest_repo.narrate_repo(str(work), budget_seconds=60)
    assert state == "done"
    assert calls[0] == "plan" and "note" in calls  # the most valuable piece is never starved by the notes
    plan = json.loads((work / "migration.json").read_text(encoding="utf-8"))
    assert "Extract `#0` first." in plan["narrative"]
    assert plan["narrative_warnings"] == []
    assert any(s["purpose"] for s in plan["subsystems"])  # analyze() re-run with the notes
    assert json.loads((work / "narratives.json").read_text(encoding="utf-8"))


def test_running_out_of_time_keeps_the_plan_and_the_notes_written_so_far(work, monkeypatch):
    calls = []
    monkeypatch.setattr(gn, "call_groq", fake_llm(calls, fail_notes_after=1))
    state = ingest_repo.narrate_repo(str(work), budget_seconds=60)
    assert state == "partial"
    assert calls.count("note") == 2  # one written, the second hit the budget and the loop stopped (no more calls)
    plan = json.loads((work / "migration.json").read_text(encoding="utf-8"))
    assert plan["narrative"]
    assert len(json.loads((work / "narratives.json").read_text(encoding="utf-8"))) == 1


def test_a_superseded_run_never_overwrites_newer_results(work, monkeypatch):
    before = (work / "migration.json").read_bytes()
    monkeypatch.setattr(gn, "call_groq", fake_llm([]))
    assert ingest_repo.narrate_repo(str(work), budget_seconds=60, still_current=lambda: False) == "skipped"
    assert (work / "migration.json").read_bytes() == before
    assert json.loads((work / "narratives.json").read_text(encoding="utf-8")) == {}


def test_no_key_means_skipped_not_an_error(work, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    assert ingest_repo.narrate_repo(str(work), budget_seconds=60) == "skipped"


# ------------------------------------------------------------------ the daily allowance

DAILY = {"error": {"message": "Rate limit reached for model `openai/gpt-oss-120b` on tokens per day (TPD): Limit 200000, Used 198607, Requested 1632. Please try again in 1m43s."}}
PER_MINUTE = {"error": {"message": "Rate limit reached for model `openai/gpt-oss-120b` on tokens per minute (TPM): Limit 8000, Used 7900, Requested 600."}}


def test_a_spent_daily_allowance_is_a_hard_stop_not_a_wait(monkeypatch):
    """Groq answers 429 for both limits. Waiting a minute fixes the per-minute one; for the daily one it only burns
    time (it cost a real analysis minutes while the allowance was already spent)."""
    slept, posts = [], []
    monkeypatch.setattr(gn.time, "sleep", slept.append)
    monkeypatch.setattr(gn.requests, "post", lambda *a, **k: posts.append(1) or FakeResponse(429, {"retry-after": "104"}, DAILY))
    gn.take_quota_flag()
    with pytest.raises(gn.NarrationQuotaExhausted):  # note: no deadline, as in a local run
        call_groq("p", "key", "model")
    assert slept == [] and len(posts) == 1
    assert gn.take_quota_flag() is True and gn.take_quota_flag() is False  # reported once


def test_a_per_minute_limit_is_still_waited_out(monkeypatch):
    slept = []
    monkeypatch.setattr(gn.time, "sleep", slept.append)
    replies = iter([FakeResponse(429, {"retry-after": "9"}, PER_MINUTE), ok("fine")])
    monkeypatch.setattr(gn.requests, "post", lambda *a, **k: next(replies))
    assert call_groq("p", "key", "model") == "fine"
    assert slept == [9.0]


def test_the_state_says_quota_when_the_daily_allowance_is_why_nothing_was_written(work, monkeypatch):
    monkeypatch.setattr(gn.requests, "post", lambda *a, **k: FakeResponse(429, {"retry-after": "104"}, DAILY))
    before = (work / "migration.json").read_bytes()
    assert ingest_repo.narrate_repo(str(work), budget_seconds=60) == "quota"
    plan = json.loads((work / "migration.json").read_text(encoding="utf-8"))
    assert plan["narrative"] is None and plan["summary"]["subsystems"] > 0  # the measured plan is untouched in substance
    assert before and json.loads((work / "narratives.json").read_text(encoding="utf-8")) == {}


def test_prose_that_was_written_before_the_allowance_ran_out_is_partial_not_quota(work, monkeypatch):
    replies = iter([ok("## Target shape\nExtract `#0` first.")])

    def post(*args, **kwargs):
        try:
            return next(replies)
        except StopIteration:
            return FakeResponse(429, {"retry-after": "104"}, DAILY)

    monkeypatch.setattr(gn.requests, "post", post)
    assert ingest_repo.narrate_repo(str(work), budget_seconds=60) == "partial"
