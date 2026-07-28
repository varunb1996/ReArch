"""M6: bare-bones API serving graph queries as JSON.

Hardcoded to the one fixture repo's pre-built Kuzu DB — no auth, no upload,
no multi-repo support yet. Those come in M7/M8. Run with:
    .venv/Scripts/uvicorn api.main:app --reload --port 8000
"""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.services.graph_store import GraphStore

DB_PATH = os.environ.get("REARCH_DB_PATH") or str(Path(__file__).resolve().parent / "data" / "kuzu_db")

app = FastAPI(title="ReArch API (dev)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_store: GraphStore | None = None


def get_store() -> GraphStore:
    global _store
    if _store is None:
        _store = GraphStore(DB_PATH)
    return _store


@app.get("/api/graph")
def get_graph():
    store = get_store()
    return {"nodes": store.all_nodes(), "edges": store.all_edges()}
