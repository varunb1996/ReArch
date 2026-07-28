"""M9: semantic clustering — group functions/methods/classes into candidate
subsystems by embedding structured facts about each node, not raw source.

Each node's embedding text is: its name/kind/path, its parameters, and its
immediate callers/callees pulled from the already-resolved graph. This keeps
the same anti-hallucination discipline as the rest of the pipeline (grounded
in verified structure) and keeps embedding volume small and cheap, which
matters once this moves to a rate-limited hosted embedding API later.

Uses fastembed (ONNX, local, no torch/account/API key) + scikit-learn's
HDBSCAN (no need to pick k upfront; leaves genuinely uncategorizable nodes
as noise (-1) instead of forcing them into a cluster).

Usage:
    .venv/Scripts/python parser/pipeline/cluster_nodes.py <graph.json> <output_graph.json>
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding
from sklearn.cluster import HDBSCAN
from sklearn.preprocessing import normalize

CLUSTERABLE_KINDS = {"function", "method", "class"}
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


def build_node_text(node: dict, callees: list[str], callers: list[str]) -> str:
    parts = [f"{node['kind']} {node['qualified_name']} in {node.get('path', '')}"]
    params = node.get("params")
    if params:
        parts.append("params: " + ", ".join(params))
    if callees:
        parts.append("calls: " + ", ".join(sorted(set(callees))[:10]))
    if callers:
        parts.append("called by: " + ", ".join(sorted(set(callers))[:10]))
    return ". ".join(parts)


def main(graph_path: str, output_path: str, min_cluster_size: int = 3) -> None:
    graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}

    callees: dict[str, list[str]] = defaultdict(list)
    callers: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["kind"] != "calls":
            continue
        src, tgt = nodes_by_id.get(edge["source"]), nodes_by_id.get(edge["target"])
        if src and tgt:
            callees[edge["source"]].append(tgt["name"])
            callers[edge["target"]].append(src["name"])

    clusterable = [n for n in graph["nodes"] if n["kind"] in CLUSTERABLE_KINDS]

    for node in graph["nodes"]:
        node["cluster"] = -1

    if len(clusterable) <= min_cluster_size:
        print(f"Only {len(clusterable)} clusterable nodes (need > {min_cluster_size}); leaving all unclustered.")
        Path(output_path).write_text(json.dumps(graph, indent=2), encoding="utf-8")
        return

    texts = [build_node_text(n, callees.get(n["id"], []), callers.get(n["id"], [])) for n in clusterable]

    print(f"Embedding {len(texts)} nodes with {EMBEDDING_MODEL}...")
    model = TextEmbedding(model_name=EMBEDDING_MODEL)
    vectors = normalize(np.array(list(model.embed(texts))))

    print("Clustering...")
    labels = HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(vectors)

    for node, label in zip(clusterable, labels):
        node["cluster"] = int(label)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int((labels == -1).sum())
    print(f"Found {n_clusters} clusters, {n_noise} noise nodes out of {len(labels)} clusterable nodes.")

    Path(output_path).write_text(json.dumps(graph, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: cluster_nodes.py <graph.json> <output_graph.json>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
