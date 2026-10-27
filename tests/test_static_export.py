"""The Pages demo has no server: these files ARE its API. They must match the
shapes the real API returns, or the UI breaks only once deployed."""
import json

from parser.pipeline.migration_plan import analyze
from scripts.export_static_demo import api_shaped_graph, main as export


def node(name, cluster=0):
    return {"id": f"python:{name}.py:{name}", "kind": "function", "language": "python", "path": f"{name}.py",
            "name": name, "qualified_name": name, "start_line": 1, "end_line": 5, "cluster": cluster, "params": ["x"]}


def test_graph_gets_placeholder_nodes_for_unresolved_targets_and_drops_unused_fields():
    a = node("a")
    graph = {"nodes": [a], "edges": [{"source": a["id"], "target": "unresolved:json.loads", "kind": "calls",
                                      "resolution": "unresolved", "external": {"package": "json", "kind": "stdlib"}}]}
    shaped = api_shaped_graph(graph)
    placeholder = next(n for n in shaped["nodes"] if n["kind"] == "unresolved")
    assert placeholder["id"] == "unresolved:json.loads" and placeholder["cluster"] == -1
    assert "params" not in shaped["nodes"][0] and "external" not in shaped["edges"][0]
    assert set(shaped["edges"][0]) == {"source", "target", "kind", "resolution", "evidence"}


def test_export_writes_everything_the_ui_requests_and_an_index(tmp_path):
    folder = tmp_path / "demo" / "sample"
    folder.mkdir(parents=True)
    a, b = node("a", 0), node("b", 1)
    graph = {"nodes": [a, b], "edges": [{"source": a["id"], "target": b["id"], "kind": "calls", "resolution": "resolved"}]}
    (folder / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (folder / "migration.json").write_text(json.dumps(analyze(graph)), encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"name": "Sample", "git_url": "https://github.com/o/sample", "commit": "abc123"}), encoding="utf-8")

    index = export(tmp_path / "demo", tmp_path / "out")

    assert index == json.loads((tmp_path / "out" / "index.json").read_text(encoding="utf-8"))
    assert index[0]["id"] == "sample" and index[0]["status"] == "ready" and index[0]["demo"] is True and index[0]["commit"] == "abc123"
    out = tmp_path / "out" / "sample"
    assert {p.name for p in out.iterdir()} == {"graph.json", "narratives.json", "migration.json", "migration.md"}
    assert json.loads((out / "narratives.json").read_text(encoding="utf-8")) == {}   # optional file defaults to empty
    assert (out / "migration.md").read_text(encoding="utf-8").startswith("# Migration plan: Sample")
