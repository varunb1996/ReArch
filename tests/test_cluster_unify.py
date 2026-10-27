from parser.pipeline.cluster_nodes import unify_subtrees


def sym(qualified_name, kind="function", path="m.py"):
    return {"id": f"{path}:{qualified_name}", "path": path, "qualified_name": qualified_name, "kind": kind}


def test_class_and_methods_share_the_majority_cluster():
    nodes = [sym("Store", "class"), sym("Store.get", "method"), sym("Store.put", "method"), sym("Store.close", "method")]
    labels = {nodes[0]["id"]: 7, nodes[1]["id"]: 2, nodes[2]["id"]: 2, nodes[3]["id"]: 2}
    result = unify_subtrees(nodes, labels)
    assert set(result.values()) == {2}


def test_noise_members_are_adopted_but_noise_alone_stays_noise():
    nodes = [sym("A", "class"), sym("A.run", "method"), sym("lonely")]
    labels = {nodes[0]["id"]: 4, nodes[1]["id"]: -1, nodes[2]["id"]: -1}
    result = unify_subtrees(nodes, labels)
    assert result[nodes[1]["id"]] == 4
    assert result[nodes[2]["id"]] == -1


def test_nested_functions_follow_their_outermost_function():
    nodes = [sym("extract"), sym("extract.walk"), sym("extract.walk.inner")]
    labels = {nodes[0]["id"]: 1, nodes[1]["id"]: 1, nodes[2]["id"]: 5}
    assert set(unify_subtrees(nodes, labels).values()) == {1}


def test_same_qualified_name_in_different_files_is_not_merged():
    a, b = sym("run", path="a.py"), sym("run", path="b.py")
    result = unify_subtrees([a, b], {a["id"]: 1, b["id"]: 2})
    assert (result[a["id"]], result[b["id"]]) == (1, 2)


# --- granularity control -------------------------------------------------------

import numpy as np

from parser.pipeline.cluster_nodes import (
    adopt_by_graph_affinity, merge_to_target, renumber_by_size, target_cluster_count,
)


def test_target_count_grows_slowly_with_repo_size():
    assert target_cluster_count(10) == 5            # never fewer than 5
    assert target_cluster_count(113) == 7
    assert target_cluster_count(321) == 13
    assert target_cluster_count(3000) == 38


def test_merge_folds_the_smallest_cluster_into_its_nearest_neighbour():
    vectors = np.array([[1, 0], [1, 0.1], [0, 1], [0, 1.1], [0.9, 0.1]], dtype=float)
    labels = np.array([0, 0, 1, 1, 2])  # cluster 2 is tiny and points the same way as cluster 0
    merged = merge_to_target(labels, vectors, target=2)
    assert merged[4] == merged[0] != merged[2]


def test_merge_leaves_noise_alone_and_is_a_noop_under_target():
    vectors = np.eye(3)
    labels = np.array([0, -1, 1])
    assert merge_to_target(labels, vectors, target=5).tolist() == [0, -1, 1]
    assert merge_to_target(np.array([0, -1, 1]), vectors, target=1).tolist()[1] == -1


def edge(src, dst, resolution="resolved"):
    return {"source": src, "target": dst, "kind": "calls", "resolution": resolution}


def test_unassigned_node_joins_the_subsystem_it_mostly_talks_to():
    labels = {"a": 1, "b": 1, "c": 2, "lonely": -1}
    edges = [edge("lonely", "a"), edge("b", "lonely"), edge("lonely", "c")]  # 2 of 3 neighbours are in subsystem 1
    assert adopt_by_graph_affinity(labels, edges)["lonely"] == 1


def test_a_node_split_evenly_between_subsystems_stays_unassigned():
    labels = {"a": 1, "c": 2, "split": -1}
    assert adopt_by_graph_affinity(labels, [edge("split", "a"), edge("split", "c")])["split"] == -1


def test_only_resolved_edges_count_as_evidence():
    labels = {"a": 1, "x": -1}
    assert adopt_by_graph_affinity(labels, [edge("x", "a", resolution="dynamic")])["x"] == -1


def test_renumbering_puts_the_biggest_subsystem_first():
    labels = {"p": 7, "q": 7, "r": 7, "s": 3, "t": -1}
    assert renumber_by_size(labels) == {"p": 0, "q": 0, "r": 0, "s": 1, "t": -1}


def test_split_breaks_one_giant_blob_along_its_semantic_seam():
    from parser.pipeline.cluster_nodes import split_to_target
    rng = np.random.default_rng(0)
    left = rng.normal([1, 0, 0], 0.05, (8, 3))
    right = rng.normal([0, 1, 0], 0.05, (8, 3))
    vectors = np.vstack([left, right])
    labels = split_to_target(np.zeros(16, dtype=int), vectors, target=2)
    assert len(set(labels.tolist())) == 2
    assert len(set(labels[:8].tolist())) == 1 and len(set(labels[8:].tolist())) == 1   # no mixing


def test_split_stops_when_nothing_is_big_enough_and_leaves_noise_alone():
    from parser.pipeline.cluster_nodes import split_to_target
    vectors = np.eye(4)
    labels = np.array([0, 0, -1, 1])
    assert split_to_target(labels, vectors, target=10).tolist() == [0, 0, -1, 1]
