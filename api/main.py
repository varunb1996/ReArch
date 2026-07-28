"""M8: multi-user API — repos are owned by a user, and one user can never
see another user's repos or graphs.

Identity here is a dev-only `X-User` header trusted as-is — there is no
password, no token verification. This is NOT real auth; it exists so the
isolation logic below (a user can only ever read their own repos) can be
built and demoed today. The swap-in point for real auth later is exactly
one function: get_current_user(). Everything downstream of it (ownership
checks, per-user repo lists) doesn't change when that swap happens.

Run with:
    .venv/Scripts/uvicorn api.main:app --reload --port 8000
"""
import hashlib
import hmac
import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.services.graph_store import GraphStore
from api.services.user_store import UserStore
from parser.pipeline.diff_graphs import diff_graphs
from parser.pipeline.ingest_repo import main as ingest_repo_main

load_dotenv()

DATA_ROOT = Path(__file__).resolve().parent / "data"
REPOS_ROOT = DATA_ROOT / "repos"
USER_DB_PATH = DATA_ROOT / "users.sqlite3"

app = FastAPI(title="ReArch API (dev)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_user_store: UserStore | None = None
_graph_stores: dict[str, GraphStore] = {}


def get_user_store() -> UserStore:
    global _user_store
    if _user_store is None:
        _user_store = UserStore(str(USER_DB_PATH))
    return _user_store


def get_graph_store(repo_id: str) -> GraphStore:
    if repo_id not in _graph_stores:
        _graph_stores[repo_id] = GraphStore(str(REPOS_ROOT / repo_id / "kuzu_db"))
    return _graph_stores[repo_id]


def reset_graph_store(repo_id: str) -> GraphStore:
    """Wipe and recreate the Kuzu DB for a repo. Required before re-analysis:
    Kuzu's MERGE only adds/updates, so without a full reset, nodes/edges
    removed since the last analysis would linger as stale data forever."""
    existing = _graph_stores.pop(repo_id, None)
    if existing is not None:
        existing.close()
    db_path = REPOS_ROOT / repo_id / "kuzu_db"
    for path in (db_path, db_path.with_name(db_path.name + ".wal")):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    return get_graph_store(repo_id)


def get_current_user(x_user: str = Header(...)) -> str:
    username = x_user.strip()
    if not username:
        raise HTTPException(400, "X-User header must be a non-empty username")
    return get_user_store().get_or_create_user(username)


def run_reanalysis(repo_id: str, git_url: str, *, fresh_db: bool) -> None:
    """Shared by the manual reanalyze endpoint, initial repo creation, and
    the git webhook — one place that runs the pipeline and updates status,
    so all three trigger paths behave identically."""
    store = get_user_store()
    work_dir = REPOS_ROOT / repo_id
    try:
        result = ingest_repo_main(git_url, str(work_dir))
        store_fn = reset_graph_store if fresh_db else get_graph_store
        store_fn(repo_id).load_graph_json(str(result["graph_path"]))
        store.set_repo_status(repo_id, "ready")
    except Exception as exc:
        store.set_repo_status(repo_id, "error", str(exc))


def normalize_git_url(url: str) -> str:
    """Strip protocol/trailing-slash/.git so URLs from different sources
    (our stored git_url vs. GitHub's webhook payload fields) compare equal."""
    url = url.strip().lower()
    for prefix in ("https://", "http://", "git@github.com:"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    return url.rstrip("/").removesuffix(".git")


class CreateRepoRequest(BaseModel):
    git_url: str
    name: str | None = None


@app.post("/api/repos")
def create_repo(body: CreateRepoRequest, user_id: str = Depends(get_current_user)):
    store = get_user_store()
    name = body.name or body.git_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    repo_id = store.create_repo(user_id, name, body.git_url)
    run_reanalysis(repo_id, body.git_url, fresh_db=False)
    return store.get_repo(repo_id)


@app.post("/api/repos/{repo_id}/reanalyze")
def reanalyze_repo(repo_id: str, user_id: str = Depends(get_current_user)):
    store = get_user_store()
    repo = store.get_repo(repo_id)
    if repo is None or repo["user_id"] != user_id:
        raise HTTPException(404, "no such repo for this user")
    run_reanalysis(repo_id, repo["git_url"], fresh_db=True)
    return store.get_repo(repo_id)


@app.post("/api/webhooks/github")
async def github_webhook(request: Request):
    """Push-triggered re-analysis — the 'real-time blueprint sync' milestone.
    Requires WEBHOOK_SECRET to be set (GitHub repo settings -> Webhooks ->
    Secret) and verifies the request really came from GitHub via HMAC-SHA256
    over the raw body, exactly as GitHub signs it. Reachability note: GitHub
    can only deliver this to a publicly reachable URL, so testing against a
    local dev server needs a tunnel (e.g. `cloudflared tunnel --url
    http://localhost:8000`) pointed at this endpoint.
    """
    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(501, "WEBHOOK_SECRET not configured; webhook disabled")

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "invalid webhook signature")

    payload = json.loads(body)
    if request.headers.get("X-GitHub-Event") != "push":
        return {"status": "ignored", "reason": "not a push event"}

    repo_info = payload.get("repository", {})
    pushed_url = repo_info.get("clone_url") or repo_info.get("html_url", "")
    pushed_normalized = normalize_git_url(pushed_url)

    store = get_user_store()
    matched = []
    for repo in store.all_repos():
        if normalize_git_url(repo["git_url"]) == pushed_normalized:
            matched.append(repo["id"])
            run_reanalysis(repo["id"], repo["git_url"], fresh_db=True)

    return {"status": "ok", "matched_repos": matched}


@app.get("/api/repos")
def list_repos(user_id: str = Depends(get_current_user)):
    return get_user_store().list_repos(user_id)


@app.get("/api/repos/{repo_id}/graph")
def get_repo_graph(repo_id: str, user_id: str = Depends(get_current_user)):
    repo = get_user_store().get_repo(repo_id)
    if repo is None or repo["user_id"] != user_id:
        raise HTTPException(404, "no such repo for this user")
    if repo["status"] != "ready":
        raise HTTPException(409, f"repo status is '{repo['status']}', not ready")

    graph_store = get_graph_store(repo_id)
    return {"nodes": graph_store.all_nodes(), "edges": graph_store.all_edges()}


@app.get("/api/repos/{repo_id}/narratives")
def get_repo_narratives(repo_id: str, user_id: str = Depends(get_current_user)):
    repo = get_user_store().get_repo(repo_id)
    if repo is None or repo["user_id"] != user_id:
        raise HTTPException(404, "no such repo for this user")

    narratives_path = REPOS_ROOT / repo_id / "narratives.json"
    if not narratives_path.exists():
        return {}
    return json.loads(narratives_path.read_text(encoding="utf-8"))


@app.get("/api/repos/{repo_id}/commits")
def list_repo_commits(repo_id: str, user_id: str = Depends(get_current_user)):
    repo = get_user_store().get_repo(repo_id)
    if repo is None or repo["user_id"] != user_id:
        raise HTTPException(404, "no such repo for this user")

    snapshots_dir = REPOS_ROOT / repo_id / "snapshots"
    if not snapshots_dir.exists():
        return []
    commits = [{"commit_sha": f.stem, "analyzed_at": f.stat().st_mtime} for f in snapshots_dir.glob("*.json")]
    commits.sort(key=lambda c: c["analyzed_at"], reverse=True)
    return commits


@app.get("/api/repos/{repo_id}/diff")
def get_repo_diff(repo_id: str, from_commit: str, to_commit: str, user_id: str = Depends(get_current_user)):
    repo = get_user_store().get_repo(repo_id)
    if repo is None or repo["user_id"] != user_id:
        raise HTTPException(404, "no such repo for this user")

    snapshots_dir = REPOS_ROOT / repo_id / "snapshots"
    old_path = snapshots_dir / f"{from_commit}.json"
    new_path = snapshots_dir / f"{to_commit}.json"
    if not old_path.exists() or not new_path.exists():
        raise HTTPException(404, "one or both commit snapshots not found for this repo")

    old_graph = json.loads(old_path.read_text(encoding="utf-8"))
    new_graph = json.loads(new_path.read_text(encoding="utf-8"))
    return diff_graphs(old_graph, new_graph)
