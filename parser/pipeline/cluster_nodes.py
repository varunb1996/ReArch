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
from collections import Counter, defaultdict
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


def target_cluster_count(n: int) -> int:
    """How many subsystems a repo of n symbols should be reported as having.

    HDBSCAN finds tight cores but reports far too many of them on larger repos
    (59 "subsystems" for a 37-file library), and a migration plan with dozens
    of subsystems is noise. Grows with the square root of size: 113 symbols
    -> 7, 321 -> 13, 800 -> 20, 3000 -> 38.
    """
    return max(5, round(n ** 0.5 * 0.7))


def merge_to_target(labels: np.ndarray, vectors: np.ndarray, target: int) -> np.ndarray:
    """Fold the smallest clusters into their nearest neighbour (cosine on the
    cluster centroids) until at most `target` remain. Noise (-1) is untouched."""
    labels = labels.copy()
    while True:
        ids = sorted(set(labels.tolist()) - {-1})
        if len(ids) <= target:
            return labels
        centroids = {i: normalize(vectors[labels == i].mean(axis=0, keepdims=True))[0] for i in ids}
        sizes = {i: int((labels == i).sum()) for i in ids}
        smallest = min(ids, key=lambda i: (sizes[i], i))
        nearest = max((j for j in ids if j != smallest), key=lambda j: (float(centroids[smallest] @ centroids[j]), -j))
        labels[labels == smallest] = nearest


def adopt_by_graph_affinity(labels: dict[str, int], edges: list[dict]) -> dict[str, int]:
    """Give each unassigned node to the subsystem it mostly talks to.

    Uses only resolved edges in both directions and needs a strict majority
    (more than half of its clustered neighbours), so a function that straddles
    two subsystems, including an exact tie, stays unassigned instead of being
    guessed into one.
    """
    neighbours: dict[str, Counter] = defaultdict(Counter)
    for edge in edges:
        if edge["resolution"] != "resolved":
            continue
        src, dst = edge["source"], edge["target"]
        if src in labels and dst in labels:
            if labels[dst] >= 0:
                neighbours[src][labels[dst]] += 1
            if labels[src] >= 0:
                neighbours[dst][labels[src]] += 1
    adopted = dict(labels)
    for node_id, label in labels.items():
        votes = neighbours.get(node_id)
        if label < 0 and votes:
            winner, count = votes.most_common(1)[0]
            if count / sum(votes.values()) > 0.5:
                adopted[node_id] = winner
    return adopted


def renumber_by_size(labels: dict[str, int]) -> dict[str, int]:
    """Subsystem #0 is the biggest; ids are contiguous."""
    sizes = Counter(label for label in labels.values() if label >= 0)
    order = {old: new for new, (old, _) in enumerate(sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0])))}
    return {node_id: order.get(label, -1) for node_id, label in labels.items()}


def unify_subtrees(nodes: list[dict], labels: dict[str, int]) -> dict[str, int]:
    """Make every symbol share a cluster with its outermost enclosing class/function.

    Embedding similarity happily splits a class from its own methods, which is
    meaningless for migration: you cannot move half a class into another
    service, and the split would show up as fake coupling between subsystems.
    The group's cluster is the majority of its members' clusters; noise (-1)
    only counts if no member was clustered at all.
    """
    by_name = {(n["path"], n["qualified_name"]): n["id"] for n in nodes}

    def outermost(n: dict) -> str:
        parts = n["qualified_name"].split(".")
        for i in range(1, len(parts)):
            ancestor = by_name.get((n["path"], ".".join(parts[:i])))
            if ancestor:
                return ancestor
        return n["id"]

    groups: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        groups[outermost(n)].append(n["id"])

    unified = dict(labels)
    for ids in groups.values():
        votes = Counter(labels[i] for i in ids if labels[i] >= 0)
        winner = votes.most_common(1)[0][0] if votes else -1
        for i in ids:
            unified[i] = winner
    return unified


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

    labels = merge_to_target(labels, vectors, target_cluster_count(len(clusterable)))
    assigned = adopt_by_graph_affinity({n["id"]: int(label) for n, label in zip(clusterable, labels)}, graph["edges"])
    unified = renumber_by_size(unify_subtrees(clusterable, assigned))
    for node in clusterable:
        node["cluster"] = unified[node["id"]]

    final = [n["cluster"] for n in clusterable]
    n_clusters = len({c for c in final if c >= 0})
    n_noise = sum(1 for c in final if c < 0)
    print(f"Found {n_clusters} clusters, {n_noise} unassigned out of {len(final)} clusterable nodes.")

    Path(output_path).write_text(json.dumps(graph, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: cluster_nodes.py <graph.json> <output_graph.json>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
