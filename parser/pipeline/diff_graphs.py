"""M13: version-aware diffing between two analyzed-commit graph snapshots.

Diffing is tractable specifically because node IDs are stable across commits
(language:path:qualified_name — never a DB auto-increment ID, per the schema
in parser/schema/graph-node.schema.json). A rename or move shows up as an
add+remove of a differently-named node rather than false churn on every
unrelated node — see the project plan's Risk #1 for why that design choice
was made all the way back in M1's schema.

Usage:
    .venv/Scripts/python parser/pipeline/diff_graphs.py <old.json> <new.json> <output_diff.json>
"""
import json
import sys
from pathlib import Path


def load_snapshot(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def edge_key(e: dict) -> tuple:
    return (e["source"], e["target"], e["kind"])


def diff_graphs(old: dict, new: dict) -> dict:
    old_nodes = {n["id"]: n for n in old["nodes"]}
    new_nodes = {n["id"]: n for n in new["nodes"]}

    nodes_added = [new_nodes[i] for i in new_nodes.keys() - old_nodes.keys()]
    nodes_removed = [old_nodes[i] for i in old_nodes.keys() - new_nodes.keys()]

    old_edges = {edge_key(e): e for e in old["edges"]}
    new_edges = {edge_key(e): e for e in new["edges"]}

    edges_added = [new_edges[k] for k in new_edges.keys() - old_edges.keys()]
    edges_removed = [old_edges[k] for k in old_edges.keys() - new_edges.keys()]

    resolution_changed = []
    for k in old_edges.keys() & new_edges.keys():
        if old_edges[k]["resolution"] != new_edges[k]["resolution"]:
            resolution_changed.append({
                "source": k[0], "target": k[1], "kind": k[2],
                "old_resolution": old_edges[k]["resolution"],
                "new_resolution": new_edges[k]["resolution"],
            })

    cluster_changed = []
    for node_id in old_nodes.keys() & new_nodes.keys():
        old_cluster = old_nodes[node_id].get("cluster", -1)
        new_cluster = new_nodes[node_id].get("cluster", -1)
        if old_cluster != new_cluster:
            cluster_changed.append({
                "id": node_id,
                "qualified_name": new_nodes[node_id]["qualified_name"],
                "old_cluster": old_cluster,
                "new_cluster": new_cluster,
            })

    return {
        "nodes_added": sorted(nodes_added, key=lambda n: n["id"]),
        "nodes_removed": sorted(nodes_removed, key=lambda n: n["id"]),
        "edges_added": sorted(edges_added, key=lambda e: (e["source"], e["target"])),
        "edges_removed": sorted(edges_removed, key=lambda e: (e["source"], e["target"])),
        "edges_resolution_changed": resolution_changed,
        "cluster_membership_changed": cluster_changed,
        "summary": {
            "nodes_added": len(nodes_added),
            "nodes_removed": len(nodes_removed),
            "edges_added": len(edges_added),
            "edges_removed": len(edges_removed),
            "resolution_changed": len(resolution_changed),
            "cluster_membership_changed": len(cluster_changed),
        },
    }


def main(old_path: str, new_path: str, output_path: str) -> None:
    diff = diff_graphs(load_snapshot(old_path), load_snapshot(new_path))
    Path(output_path).write_text(json.dumps(diff, indent=2), encoding="utf-8")
    s = diff["summary"]
    print(
        f"+{s['nodes_added']} -{s['nodes_removed']} nodes, "
        f"+{s['edges_added']} -{s['edges_removed']} edges, "
        f"{s['resolution_changed']} resolution changes, "
        f"{s['cluster_membership_changed']} cluster moves"
    )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: diff_graphs.py <old_snapshot.json> <new_snapshot.json> <output_diff.json>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
