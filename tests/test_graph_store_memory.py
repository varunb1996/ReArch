"""A small host cannot keep one open Kùzu database (each with its own buffer pool) per analyzed repo."""
import json

import pytest

import api.main as main
from api.services.graph_store import GraphStore


def write_graph(folder, marker):
    folder.mkdir(parents=True, exist_ok=True)
    graph = {"nodes": [{"id": f"python:{marker}.py:f", "kind": "function", "language": "python", "path": f"{marker}.py",
                        "name": "f", "qualified_name": "f", "start_line": 1, "end_line": 2, "cluster": 0}], "edges": []}
    path = folder / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return path


def node_count(store):
    return store.query("MATCH (n:Node) RETURN count(n)")[0][0]


def test_a_closed_store_reopens_itself_with_its_data(tmp_path):
    store = GraphStore(str(tmp_path / "db"))
    store.load_graph_json(str(write_graph(tmp_path / "g", "a")))
    assert not store.is_open  # a bulk load releases its memory when it finishes
    assert node_count(store) == 1  # a request that still holds the reference keeps working
    assert store.is_open
    store.close()
    store.close()  # closing twice is harmless


def test_loading_a_graph_releases_the_database_but_keeps_the_data(tmp_path):
    store = GraphStore(str(tmp_path / "db"))
    assert store.is_open
    store.load_graph_json(str(write_graph(tmp_path / "g", "a")))
    assert not store.is_open
    assert store.query("MATCH (n:Node) RETURN n.qualified_name")[0][0] == "f"


@pytest.fixture
def bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "REPOS_ROOT", tmp_path / "repos")
    monkeypatch.setattr(main, "_graph_stores", {})
    monkeypatch.setattr(main, "MAX_OPEN_GRAPHS", 2)
    yield
    for store in main._graph_stores.values():
        store.close()


def test_only_the_most_recently_used_stores_stay_open(bounded, tmp_path):
    stores = {}
    for repo_id in ("r1", "r2", "r3", "r4"):
        (main.REPOS_ROOT / repo_id).mkdir(parents=True)  # the analysis job creates the folder before opening its DB
        stores[repo_id] = main.get_graph_store(repo_id)
        stores[repo_id].load_graph_json(str(write_graph(tmp_path / "src" / repo_id, repo_id)))
        node_count(stores[repo_id])  # a repo being browsed: its store is open
    open_now = {rid for rid, st in main._graph_stores.items() if st.is_open}
    assert open_now == {"r3", "r4"}
    # an evicted repo is still fully readable, and reading it makes it recent again
    assert node_count(main.get_graph_store("r1")) == 1
    assert {rid for rid, st in main._graph_stores.items() if st.is_open} == {"r4", "r1"}


def test_unbounded_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "REPOS_ROOT", tmp_path / "repos")
    monkeypatch.setattr(main, "_graph_stores", {})
    monkeypatch.setattr(main, "MAX_OPEN_GRAPHS", 0)
    for repo_id in ("a", "b", "c"):
        (main.REPOS_ROOT / repo_id).mkdir(parents=True)
        main.get_graph_store(repo_id)
    assert all(st.is_open for st in main._graph_stores.values())
    for store in main._graph_stores.values():
        store.close()


def test_batched_loader_keeps_every_node_edge_and_property(tmp_path, monkeypatch):
    """Loading is batched for speed; batch boundaries must not drop or duplicate anything."""
    from api.services import graph_store

    monkeypatch.setattr(graph_store, "LOAD_BATCH", 3)  # force several batches out of a small graph
    nodes = [{"id": f"python:m.py:f{i}", "kind": "function", "language": "python", "path": "m.py", "name": f"f{i}",
              "qualified_name": f"f{i}", "start_line": i, "end_line": i + 1, "cluster": i % 2} for i in range(8)]
    nasty = 'evidence with "quotes", commas,\nnewlines and unicode é中'
    edges = [{"source": nodes[i]["id"], "target": nodes[i + 1]["id"], "kind": kind, "resolution": "resolved", "evidence": nasty if i == 0 else None}
             for i in range(7) for kind in ("calls", "imports")]
    edges.append({"source": nodes[0]["id"], "target": nodes[1]["id"], "kind": "inherits", "resolution": "resolved"})
    edges.append({"source": nodes[2]["id"], "target": "unresolved:requests.get", "kind": "calls", "resolution": "unresolved"})
    edges.append({"source": nodes[3]["id"], "target": "unresolved:requests.get", "kind": "calls", "resolution": "unresolved"})
    path = tmp_path / "graph.json"
    path.write_text(json.dumps({"nodes": nodes, "edges": edges}), encoding="utf-8")

    store = GraphStore(str(tmp_path / "db"))
    store.load_graph_json(str(path))
    assert node_count(store) == 9  # 8 symbols + ONE placeholder shared by two edges
    placeholder = store.query("MATCH (n:Node {kind: 'unresolved'}) RETURN n.id, n.name")
    assert placeholder == [["unresolved:requests.get", "requests.get"]]
    counts = {t: store.query(f"MATCH ()-[r:{t}]->() RETURN count(r)")[0][0] for t in ("CALLS", "IMPORTS", "INHERITS")}
    assert counts == {"CALLS": 7 + 2, "IMPORTS": 7, "INHERITS": 1}
    assert store.query("MATCH (a:Node {id: $id})-[r:CALLS]->() RETURN r.evidence", {"id": nodes[0]["id"]}) == [[nasty]]
    assert store.query("MATCH (n:Node {id: $id}) RETURN n.start_line, n.end_line, n.cluster", {"id": nodes[5]["id"]}) == [[5, 6, 1]]

    store.load_graph_json(str(path))  # reloading the same graph must not duplicate nodes
    assert node_count(store) == 9
