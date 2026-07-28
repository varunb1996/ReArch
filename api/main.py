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
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.services.graph_store import GraphStore
from api.services.user_store import UserStore
from parser.pipeline.ingest_repo import main as ingest_repo_main

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


def get_current_user(x_user: str = Header(...)) -> str:
    username = x_user.strip()
    if not username:
        raise HTTPException(400, "X-User header must be a non-empty username")
    return get_user_store().get_or_create_user(username)


class CreateRepoRequest(BaseModel):
    git_url: str
    name: str | None = None


@app.post("/api/repos")
def create_repo(body: CreateRepoRequest, user_id: str = Depends(get_current_user)):
    store = get_user_store()
    name = body.name or body.git_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    repo_id = store.create_repo(user_id, name, body.git_url)

    work_dir = REPOS_ROOT / repo_id
    try:
        graph_path = ingest_repo_main(body.git_url, str(work_dir))
        get_graph_store(repo_id).load_graph_json(str(graph_path))
        store.set_repo_status(repo_id, "ready")
    except Exception as exc:
        store.set_repo_status(repo_id, "error", str(exc))

    return store.get_repo(repo_id)


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
