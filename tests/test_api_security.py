"""The guarantees a public deployment depends on: who can read what, what
URLs get cloned, and how hard one visitor can lean on a small server."""
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.main as main
from api import security

ALICE = "a" * 20
BOB = "b" * 20


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REARCH_PUBLIC_MODE", "1")
    monkeypatch.setattr(main, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(main, "REPOS_ROOT", tmp_path / "repos")
    monkeypatch.setattr(main, "USER_DB_PATH", tmp_path / "users.sqlite3")
    monkeypatch.setattr(main, "_user_store", None)
    monkeypatch.setattr(main, "_graph_stores", {})
    monkeypatch.setattr(security, "user_limiter", security.RateLimiter(3, 3600))
    monkeypatch.setattr(security, "ip_limiter", security.RateLimiter(100, 3600))
    return TestClient(main.app)  # no `with`: lifespan (demo seeding) stays off


def add_repo(owner_name, name="repo", plan=None):
    store = main.get_user_store()
    repo_id = store.create_repo(store.get_or_create_user(owner_name), name, "https://github.com/o/r")
    store.set_repo_status(repo_id, "ready")
    folder = main.REPOS_ROOT / repo_id
    folder.mkdir(parents=True)
    (folder / "narratives.json").write_text("{}", encoding="utf-8")
    if plan:
        (folder / "migration.json").write_text(json.dumps(plan), encoding="utf-8")
    return repo_id


def get(client, path, user):
    return client.get(path, headers={"X-User": user})


# --- git URL validation ------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://github.com/pallets/flask",
    "https://github.com/pallets/flask.git",
    "https://github.com/pallets/flask/",
])
def test_public_mode_accepts_plain_github_urls(client, url):
    assert security.validate_git_url(url) == url


@pytest.mark.parametrize("url", [
    "/etc",
    "C:/Users/someone/secret",
    "file:///etc/passwd",
    "ssh://git@github.com/o/r",
    "https://evil.example/o/r",
    "https://github.com.evil.example/o/r",
    "https://github.com/o/r/../../x",
    "--upload-pack=touch /tmp/pwned",
    "https://github.com/o/r\n--upload-pack=x",
    "",
])
def test_public_mode_rejects_everything_else(client, url):
    with pytest.raises(HTTPException) as err:
        security.validate_git_url(url)
    assert err.value.status_code == 400


def test_option_lookalikes_are_rejected_even_in_local_mode(monkeypatch):
    monkeypatch.delenv("REARCH_PUBLIC_MODE", raising=False)
    assert security.validate_git_url("C:/local/repo") == "C:/local/repo"  # local dev stays permissive
    with pytest.raises(HTTPException):
        security.validate_git_url("--upload-pack=x")


# --- identity ----------------------------------------------------------------

def test_short_guessable_usernames_are_refused_in_public_mode(client):
    assert get(client, "/api/repos", "alice").status_code == 401
    assert get(client, "/api/repos", ALICE).status_code == 200
    assert get(client, "/api/repos", "demo").status_code == 200


# --- isolation and the read-only demo -----------------------------------------

def test_one_visitor_cannot_read_anothers_repo(client):
    repo_id = add_repo(ALICE)
    assert get(client, f"/api/repos/{repo_id}/narratives", ALICE).status_code == 200
    assert get(client, f"/api/repos/{repo_id}/narratives", BOB).status_code == 404
    assert get(client, f"/api/repos/{repo_id}/migration", BOB).status_code == 404


def test_demo_repos_are_readable_by_everyone_and_writable_by_no_one(client):
    demo_repo = add_repo("demo", name="example", plan={"summary": {}, "subsystems": []})
    assert get(client, f"/api/repos/{demo_repo}/migration", BOB).status_code == 200
    assert [r["name"] for r in client.get("/api/demo/repos").json()] == ["example"]
    # nobody may re-analyze or delete-by-reanalysis a demo repo, including the demo account itself
    for user in (BOB, "demo"):
        response = client.post(f"/api/repos/{demo_repo}/reanalyze", headers={"X-User": user})
        assert response.status_code == 403


def test_demo_account_cannot_create_repos(client):
    response = client.post("/api/repos", json={"git_url": "https://github.com/o/r"}, headers={"X-User": "demo"})
    assert response.status_code == 403


def test_a_visitor_cannot_reanalyze_someone_elses_repo(client):
    repo_id = add_repo(ALICE)
    assert client.post(f"/api/repos/{repo_id}/reanalyze", headers={"X-User": BOB}).status_code == 404


def test_diff_rejects_path_traversal_in_commit_ids(client):
    repo_id = add_repo(ALICE)
    response = client.get(
        f"/api/repos/{repo_id}/diff", params={"from_commit": "../../users", "to_commit": "x"}, headers={"X-User": ALICE},
    )
    assert response.status_code == 400


def test_create_repo_rejects_non_github_urls_before_doing_any_work(client):
    response = client.post("/api/repos", json={"git_url": "/etc"}, headers={"X-User": ALICE})
    assert response.status_code == 400


# --- load protection ----------------------------------------------------------

def test_rate_limiter_blocks_after_the_limit_and_isolates_keys():
    limiter = security.RateLimiter(limit=2, window_seconds=3600)
    limiter.check("a")
    limiter.check("a")
    with pytest.raises(HTTPException) as err:
        limiter.check("a")
    assert err.value.status_code == 429
    limiter.check("b")  # a different visitor is unaffected


def test_only_one_analysis_runs_at_a_time():
    with security.analysis_slot():
        with pytest.raises(HTTPException) as err:
            with security.analysis_slot():
                pass
        assert err.value.status_code == 503
    with security.analysis_slot():  # released afterwards
        pass


def test_webhook_is_disabled_without_a_secret(client, monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    assert client.post("/api/webhooks/github", content=b"{}").status_code == 501


def test_webhook_rejects_a_bad_signature(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    response = client.post("/api/webhooks/github", content=b"{}", headers={"X-Hub-Signature-256": "sha256=00"})
    assert response.status_code == 401


# --- background analysis -------------------------------------------------------

def stub_pipeline(monkeypatch, fail_with=None):
    def fake_ingest(git_url, work_dir):
        if fail_with:
            raise fail_with
        work = Path(work_dir)
        work.mkdir(parents=True, exist_ok=True)
        (work / "graph.json").write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
        return {"graph_path": work / "graph.json", "commit_sha": "abc", "snapshot_path": None}
    monkeypatch.setattr(main, "ingest_repo_main", fake_ingest)


def create(client, user=ALICE, url="https://github.com/o/r"):
    return client.post("/api/repos", json={"git_url": url}, headers={"X-User": user})


def test_analysis_returns_pending_immediately_then_becomes_ready(client, monkeypatch):
    stub_pipeline(monkeypatch)
    response = create(client)
    assert response.status_code == 200 and response.json()["status"] == "pending"
    assert [r["status"] for r in get(client, "/api/repos", ALICE).json()] == ["ready"]


def test_the_analysis_slot_is_released_when_the_job_finishes(client, monkeypatch):
    stub_pipeline(monkeypatch)
    create(client)
    assert security.try_acquire_slot()
    security.release_slot()


def test_curated_errors_reach_the_user_but_unexpected_ones_are_hidden_in_public_mode(client, monkeypatch):
    stub_pipeline(monkeypatch, fail_with=RuntimeError("The repository has 9000 analyzable files."))
    create(client)
    assert get(client, "/api/repos", ALICE).json()[0]["error"] == "The repository has 9000 analyzable files."

    stub_pipeline(monkeypatch, fail_with=ValueError("/home/server/secret/path exploded"))
    create(client, url="https://github.com/o/other")
    errors = [r["error"] for r in get(client, "/api/repos", ALICE).json()]
    assert all("/home/server" not in e for e in errors)
    assert "Analysis failed unexpectedly. Try another repository." in errors


def test_a_visitor_can_only_start_three_analyses_per_hour(client, monkeypatch):
    stub_pipeline(monkeypatch)
    assert [create(client, url=f"https://github.com/o/r{i}").status_code for i in range(3)] == [200, 200, 200]
    assert create(client, url="https://github.com/o/r9").status_code == 429
    assert create(client, user=BOB).status_code == 200  # someone else is unaffected
    assert security.try_acquire_slot()  # a rejected request must not leak the slot
    security.release_slot()


def test_analyses_killed_by_a_restart_are_marked_failed_not_pending_forever(client):
    store = main.get_user_store()
    owner = store.get_or_create_user(ALICE)
    stuck = store.create_repo(owner, "stuck", "https://github.com/o/r")      # stays "pending"
    done = add_repo(ALICE, "done")                                            # "ready"
    assert store.fail_interrupted() == 1
    assert store.get_repo(stuck)["status"] == "error"
    assert "interrupted" in store.get_repo(stuck)["error"]
    assert store.get_repo(done)["status"] == "ready"


def test_concurrent_requests_never_see_a_missing_repo_or_crash_on_a_new_visitor(client):
    """A browser fires graph/narratives/migration at once; they used to race on one
    SQLite connection and intermittently answer 404 for a repo that exists."""
    from concurrent.futures import ThreadPoolExecutor

    repo_id = add_repo("demo", plan={"summary": {}, "subsystems": []})
    visitors = [f"{i:02d}" + "x" * 20 for i in range(8)]   # brand-new visitors, first request in parallel

    def hit(args):
        visitor, path = args
        return client.get(path, headers={"X-User": visitor}).status_code

    paths = [f"/api/repos/{repo_id}/migration", f"/api/repos/{repo_id}/narratives", "/api/repos"]
    jobs = [(v, p) for _ in range(15) for v in visitors for p in paths]
    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(hit, jobs))
    assert set(codes) == {200}, {c: codes.count(c) for c in set(codes)}
