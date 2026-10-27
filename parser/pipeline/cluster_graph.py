"""Subsystem discovery from the dependency graph alone (no embedding model).

The default clusterer (cluster_nodes.py) embeds each symbol's name and neighbours
with a sentence model. That needs ~1.2 GB of RAM at its peak, which does not fit
on a 512 MB free host, and it needs numpy/scikit-learn at runtime. This module
finds subsystems the other way round: by *coupling*. Symbols that call, inherit
from or reference each other a lot belong together, which is exactly the property
a migration plan cares about, so it is arguably the more direct signal.

Method: Louvain modularity optimisation over a weighted undirected graph made of
  - resolved dependency edges (same weights the migration engine scores coupling with),
  - a hub vertex per file and per directory, so code that sits together stays together and
    symbols with no resolved edges (common in dynamic languages) still land somewhere sensible.
Resolution is raised until at least `target` communities appear, then the smallest are folded
into their most-connected neighbour down to `target`. Pure Python, deterministic (sorted
iteration, fixed tie-breaking), memory proportional to the edge count.
"""
from collections import defaultdict
from pathlib import PurePosixPath

from parser.pipeline.migration_plan import edge_weight

FILE_TIE = 0.6        # symbol <-> its file hub
DIRECTORY_TIE = 0.25  # file hub <-> its directory hub
IMPORT_TIE = 0.3      # a file that imports a symbol is pulled towards it
MIN_COMMUNITY = 3
RESOLUTIONS = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)


def _louvain_level(adj: dict[int, dict[int, float]], resolution: float) -> tuple[dict[int, int], bool]:
    """One pass of local moving: each vertex greedily joins the neighbouring community with the best modularity gain."""
    vertices = sorted(adj)
    degree = {v: sum(w for u, w in adj[v].items() if u != v) + 2 * adj[v].get(v, 0.0) for v in vertices}
    total = sum(degree.values())
    community = {v: v for v in vertices}
    if total == 0:
        return community, False
    community_degree = dict(degree)
    improved = False
    while True:
        moved = False
        for v in vertices:
            current = community[v]
            links: dict[int, float] = defaultdict(float)
            for u, w in adj[v].items():
                if u != v:
                    links[community[u]] += w
            community_degree[current] -= degree[v]
            best, best_gain = current, links.get(current, 0.0) - resolution * community_degree[current] * degree[v] / total
            for candidate in sorted(links):
                gain = links[candidate] - resolution * community_degree[candidate] * degree[v] / total
                if gain > best_gain + 1e-12:
                    best, best_gain = candidate, gain
            community_degree[best] += degree[v]
            if best != current:
                community[v] = best
                moved = improved = True
        if not moved:
            return community, improved


def louvain(adj: dict[int, dict[int, float]], resolution: float = 1.0) -> dict[int, int]:
    """Community of every vertex of an undirected weighted graph (adjacency dict, symmetric)."""
    membership = {v: v for v in adj}
    level = {v: dict(neighbours) for v, neighbours in adj.items()}
    for _ in range(12):
        community, improved = _louvain_level(level, resolution)
        if not improved:
            break
        labels = {old: new for new, old in enumerate(sorted(set(community.values())))}
        membership = {v: labels[community[membership[v]]] for v in membership}
        merged: dict[int, dict[int, float]] = {c: defaultdict(float) for c in labels.values()}
        for v, neighbours in level.items():
            for u, w in neighbours.items():
                cv, cu = labels[community[v]], labels[community[u]]
                # every undirected edge is visited from both ends, a self-loop once
                merged[cv][cu] += w if cv != cu else (w / 2 if u != v else w)
        level = {c: dict(n) for c, n in merged.items()}
    return membership


def build_graph(nodes: list[dict], edges: list[dict]) -> tuple[dict[int, dict[int, float]], dict[str, int], int]:
    """Weighted adjacency over symbols plus file/directory hubs. Returns (adjacency, symbol id -> vertex, symbol count)."""
    index = {n["id"]: i for i, n in enumerate(sorted(nodes, key=lambda n: n["id"]))}
    adj: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))

    def tie(a: int, b: int, w: float) -> None:
        if a != b and w > 0:
            adj[a][b] += w
            adj[b][a] += w

    next_vertex = len(index)
    hubs: dict[tuple[str, str], int] = {}

    def hub(kind: str, key: str) -> int:
        nonlocal next_vertex
        if (kind, key) not in hubs:
            hubs[(kind, key)] = next_vertex
            next_vertex += 1
        return hubs[(kind, key)]

    for node in nodes:
        path = PurePosixPath(node["path"])
        file_hub = hub("file", node["path"])
        tie(index[node["id"]], file_hub, FILE_TIE)
        tie(file_hub, hub("dir", str(path.parent)), DIRECTORY_TIE)

    for edge in edges:
        if edge["resolution"] == "unresolved":
            continue
        s, t = edge["source"], edge["target"]
        if s in index and t in index:
            tie(index[s], index[t], edge_weight(edge))
        elif s.endswith(":<module>") and t in index and edge["kind"] == "imports":
            # a file referencing a class pulls that file's hub towards it
            path = s.split(":", 1)[1].rsplit(":", 1)[0]
            tie(hub("file", path), index[t], IMPORT_TIE)
    for v in range(next_vertex):
        adj[v]  # noqa: B018  (make isolated vertices exist)
    return {v: dict(n) for v, n in adj.items()}, index, len(index)


def merge_to_target(membership: dict[int, int], adj: dict[int, dict[int, float]], symbols: int, target: int) -> dict[int, int]:
    """Fold the smallest community (by symbol count) into the one it is most strongly connected to."""
    membership = dict(membership)
    while True:
        sizes: dict[int, int] = defaultdict(int)
        for v, c in membership.items():
            if v < symbols:
                sizes[c] += 1
        small = [c for c, size in sizes.items() if size < MIN_COMMUNITY]
        if len(sizes) <= target and not small:
            return membership
        if len(sizes) <= 1:
            return membership
        smallest = min(sizes, key=lambda c: (sizes[c], c))
        between: dict[int, float] = defaultdict(float)
        for v, c in membership.items():
            if c != smallest:
                continue
            for u, w in adj.get(v, {}).items():
                if membership[u] != smallest:
                    between[membership[u]] += w
        candidates = {c: w for c, w in between.items() if c in sizes}
        into = max(candidates, key=lambda c: (candidates[c], -c)) if candidates else min((c for c in sizes if c != smallest), key=lambda c: (sizes[c], c))
        for v, c in membership.items():
            if c == smallest:
                membership[v] = into


def assign_clusters(nodes: list[dict], edges: list[dict], target: int) -> dict[str, int]:
    """symbol id -> cluster id for every clusterable node (none are left unassigned)."""
    adj, index, symbols = build_graph(nodes, edges)
    membership = {}
    for resolution in RESOLUTIONS:
        membership = louvain(adj, resolution)
        if len({membership[v] for v in range(symbols)}) >= target:
            break
    membership = merge_to_target(membership, adj, symbols, target)
    return {node_id: membership[v] for node_id, v in index.items()}
