"""Graph-based subsystem discovery: known structures in, known communities out."""
import subprocess
import sys

from parser.pipeline.cluster_graph import assign_clusters, louvain


def sym(name, path):
    return {"id": f"python:{path}:{name}", "kind": "function", "path": path, "qualified_name": name, "name": name}


def call(a, b, resolution="resolved", kind="calls"):
    return {"source": a["id"], "target": b["id"], "kind": kind, "resolution": resolution}


def clique(prefix, path, size=5):
    members = [sym(f"{prefix}{i}", path) for i in range(size)]
    return members, [call(a, b) for i, a in enumerate(members) for b in members[i + 1:]]


def test_louvain_separates_two_cliques_joined_by_one_bridge():
    adj = {v: {} for v in range(10)}
    for group in (range(0, 5), range(5, 10)):
        for a in group:
            for b in group:
                if a < b:
                    adj[a][b] = adj[b][a] = 1.0
    adj[4][5] = adj[5][4] = 1.0
    membership = louvain(adj)
    assert len({membership[v] for v in range(0, 5)}) == 1
    assert len({membership[v] for v in range(5, 10)}) == 1
    assert membership[0] != membership[9]


def test_assign_clusters_recovers_two_subsystems_and_keeps_every_node():
    a_nodes, a_edges = clique("a", "billing/a.py")
    b_nodes, b_edges = clique("b", "shipping/b.py")
    bridge = call(a_nodes[0], b_nodes[0])
    nodes = a_nodes + b_nodes
    clusters = assign_clusters(nodes, a_edges + b_edges + [bridge], target=2)
    assert set(clusters) == {n["id"] for n in nodes}  # nothing left unassigned
    assert len({clusters[n["id"]] for n in a_nodes}) == 1
    assert len({clusters[n["id"]] for n in b_nodes}) == 1
    assert clusters[a_nodes[0]["id"]] != clusters[b_nodes[0]["id"]]


def test_unresolved_edges_do_not_glue_subsystems_together():
    a_nodes, a_edges = clique("a", "billing/a.py")
    b_nodes, b_edges = clique("b", "shipping/b.py")
    guesses = [call(x, y, resolution="unresolved") for x in a_nodes for y in b_nodes]
    clusters = assign_clusters(a_nodes + b_nodes, a_edges + b_edges + guesses, target=2)
    assert clusters[a_nodes[0]["id"]] != clusters[b_nodes[0]["id"]]


import pytest


@pytest.mark.parametrize("home", ["billing/a.py", "shipping/b.py"])
def test_symbols_without_any_edges_follow_their_file(home):
    """Checked in both clusters: landing in the right one by tie-break luck cannot satisfy both."""
    a_nodes, a_edges = clique("a", "billing/a.py")
    b_nodes, b_edges = clique("b", "shipping/b.py")
    loose = sym("loose_helper", home)  # no calls at all, e.g. called only through reflection
    clusters = assign_clusters(a_nodes + b_nodes + [loose], a_edges + b_edges, target=2)
    neighbour = a_nodes[0] if home.startswith("billing") else b_nodes[0]
    assert clusters[loose["id"]] == clusters[neighbour["id"]]


def test_result_is_capped_at_the_target_and_tiny_communities_are_folded():
    nodes, edges = [], []
    for g in range(8):
        members, internal = clique(f"g{g}_", f"pkg{g}/m.py", size=4)
        nodes += members
        edges += internal
    clusters = assign_clusters(nodes, edges, target=3)
    assert len(set(clusters.values())) <= 3


def test_deterministic():
    a_nodes, a_edges = clique("a", "x/a.py", 6)
    b_nodes, b_edges = clique("b", "y/b.py", 6)
    edges = a_edges + b_edges + [call(a_nodes[1], b_nodes[1])]
    first = assign_clusters(a_nodes + b_nodes, edges, target=2)
    again = assign_clusters(list(reversed(a_nodes + b_nodes)), list(reversed(edges)), target=2)
    assert first == again


def test_graph_mode_never_loads_the_embedding_stack(tmp_path):
    """The point of this mode is fitting in a small host: numpy, scikit-learn and fastembed must stay unimported."""
    import json

    a_nodes, a_edges = clique("a", "x/a.py", 6)
    b_nodes, b_edges = clique("b", "y/b.py", 6)
    graph = {"nodes": a_nodes + b_nodes, "edges": a_edges + b_edges}
    (tmp_path / "g.json").write_text(json.dumps(graph), encoding="utf-8")
    code = (
        "import sys, os;"
        "os.environ['REARCH_CLUSTERING']='graph';"
        "from parser.pipeline.cluster_nodes import main;"
        f"main(r'{tmp_path / 'g.json'}', r'{tmp_path / 'out.json'}');"
        "bad=[m for m in ('numpy','sklearn','fastembed','onnxruntime') if m in sys.modules];"
        "print('LOADED', bad)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "LOADED []" in result.stdout
    out = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert {n["cluster"] for n in out["nodes"]} == {0, 1}
