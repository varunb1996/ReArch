"""What a visitor experiences while the prose is still being written.

The analysis becomes `ready` as soon as the structure is done; the written plan and subsystem notes follow, in the
background, inside a time budget. None of the ways narration can go wrong may take the result away.
"""
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api import security

ALICE = "a" * 20
PLAN = {"summary": {"subsystems": 1}, "subsystems": [], "waves": [], "cycles": [], "narrative": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REARCH_PUBLIC_MODE", "1")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(main, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(main, "REPOS_ROOT", tmp_path / "repos")
    monkeypatch.setattr(main, "USER_DB_PATH", tmp_path / "users.sqlite3")
    monkeypatch.setattr(main, "_user_store", None)
    monkeypatch.setattr(main, "_graph_stores", {})
    monkeypatch.setattr(main, "_narration_gate", threading.Semaphore(1))
    monkeypatch.setattr(security, "user_limiter", security.RateLimiter(50, 3600))
    monkeypatch.setattr(security, "ip_limiter", security.RateLimiter(500, 3600))

    def fake_ingest(git_url, work_dir, narrate=True, narration_budget=0):
        assert narrate is False
        work = Path(work_dir)
        work.mkdir(parents=True, exist_ok=True)
        (work / "graph.json").write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
        (work / "migration.json").write_text(json.dumps(PLAN), encoding="utf-8")
        return {"graph_path": work / "graph.json", "commit_sha": "abc", "snapshot_path": None}

    monkeypatch.setattr(main, "ingest_repo_main", fake_ingest)
    return TestClient(main.app)


def new_repo():
    store = main.get_user_store()
    return store.create_repo(store.get_or_create_user(ALICE), "r", "https://github.com/o/r")


def start(repo_id):
    """Run an analysis job the way the endpoint does: it claims the slot, the job releases it."""
    assert main.try_acquire_slot()
    thread = threading.Thread(target=main.analysis_job, args=(repo_id, "https://github.com/o/r", False))
    thread.start()
    return thread


def wait_for(condition, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def migration(client, repo_id):
    return client.get(f"/api/repos/{repo_id}/migration", headers={"X-User": ALICE}).json()


class SlowNarrator:
    """Stands in for narrate_repo: blocks until released, then writes a plan unless it has been superseded."""

    def __init__(self):
        self.release = threading.Event()
        self.calls = []

    def __call__(self, work_dir, budget_seconds=0, still_current=lambda: True):
        self.calls.append(budget_seconds)
        assert self.release.wait(10)
        if not still_current():
            return "skipped"
        path = Path(work_dir) / "migration.json"
        path.write_text(json.dumps({**PLAN, "narrative": "## Target shape\nWritten."}), encoding="utf-8")
        return "done"


def test_the_repo_is_ready_and_the_slot_is_free_while_the_prose_is_still_being_written(env, monkeypatch):
    narrator = SlowNarrator()
    monkeypatch.setattr(main, "narrate_repo", narrator)
    repo_id = new_repo()
    thread = start(repo_id)

    assert wait_for(lambda: main.get_user_store().get_repo(repo_id)["status"] == "ready")
    assert wait_for(lambda: narrator.calls)  # narration has begun and is blocked
    plan = migration(env, repo_id)
    assert plan["narration"] == "running" and plan["narrative"] is None
    assert plan["summary"]["subsystems"] == 1  # the measured plan is already there

    assert main.try_acquire_slot(), "the next visitor's analysis must not wait for this one's prose"
    main.release_slot()

    narrator.release.set()
    thread.join(10)
    plan = migration(env, repo_id)
    assert plan["narration"] == "done" and "Written." in plan["narrative"]


def test_the_narration_is_given_the_configured_time_budget(env, monkeypatch):
    narrator = SlowNarrator()
    narrator.release.set()
    monkeypatch.setattr(main, "narrate_repo", narrator)
    monkeypatch.setattr(main, "NARRATION_BUDGET", 42.0)
    start(new_repo()).join(10)
    assert narrator.calls == [42.0]


def test_an_older_runs_prose_never_replaces_a_newer_analysis(env, monkeypatch):
    narrator = SlowNarrator()
    monkeypatch.setattr(main, "narrate_repo", narrator)
    repo_id = new_repo()
    thread = start(repo_id)
    assert wait_for(lambda: narrator.calls)

    main.set_narration(repo_id, "running", "a-newer-analysis")  # a re-analysis started meanwhile
    narrator.release.set()
    thread.join(10)

    assert main.get_narration(repo_id) == {"state": "running", "run": "a-newer-analysis"}
    assert migration(env, repo_id)["narrative"] is None


def test_without_a_key_there_is_nothing_to_wait_for(env, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    narrator = SlowNarrator()
    monkeypatch.setattr(main, "narrate_repo", narrator)
    repo_id = new_repo()
    start(repo_id).join(10)
    assert main.get_user_store().get_repo(repo_id)["status"] == "ready"
    assert migration(env, repo_id)["narration"] == "unavailable"
    assert narrator.calls == []


def test_a_busy_key_skips_the_prose_instead_of_queueing_forever(env, monkeypatch):
    narrator = SlowNarrator()
    monkeypatch.setattr(main, "narrate_repo", narrator)
    monkeypatch.setattr(main, "NARRATION_BUDGET", 0.3)
    main._narration_gate.acquire()  # another visitor's narration holds the shared key
    try:
        repo_id = new_repo()
        start(repo_id).join(10)
    finally:
        main._narration_gate.release()
    assert main.get_user_store().get_repo(repo_id)["status"] == "ready"
    assert migration(env, repo_id)["narration"] == "skipped"
    assert narrator.calls == []


def test_a_crash_while_writing_prose_leaves_a_usable_analysis(env, monkeypatch):
    def explode(*args, **kwargs):
        raise ConnectionError("groq is down")

    monkeypatch.setattr(main, "narrate_repo", explode)
    repo_id = new_repo()
    start(repo_id).join(10)
    assert main.get_user_store().get_repo(repo_id)["status"] == "ready"
    assert migration(env, repo_id)["narration"] == "skipped"
    assert main._narration_gate.acquire(blocking=False), "the gate must be released after a crash"
    main._narration_gate.release()


def test_analyses_from_before_this_change_report_done(env):
    repo_id = new_repo()
    work = main.REPOS_ROOT / repo_id
    work.mkdir(parents=True)
    (work / "migration.json").write_text(json.dumps(PLAN), encoding="utf-8")
    main.get_user_store().set_repo_status(repo_id, "ready")
    assert migration(env, repo_id)["narration"] == "done"
