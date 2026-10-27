"""Unit tests for the migration engine on small synthetic graphs.

Each graph is built so the right answer is obvious by inspection: a clean
dependency chain, a two-subsystem cycle with one weak and one strong link, and
an unassigned node that must not distort anyone's metrics.
"""
from parser.pipeline.migration_plan import (
    analyze, assign_subsystems, check_references, looks_like_project_call, merged_line_count,
)


def node(name, cluster, path=None, kind="function", start=1, end=10):
    path = path or f"{name.lower()}.py"
    return {
        "id": f"python:{path}:{name}", "kind": kind, "language": "python", "path": path,
        "name": name, "qualified_name": name, "start_line": start, "end_line": end, "cluster": cluster,
    }


def edge(src, dst, resolution="resolved", kind="calls", **extra):
    return {"source": src["id"], "target": dst["id"], "kind": kind, "resolution": resolution, **extra}


def by_id(plan):
    return {s["id"]: s for s in plan["subsystems"]}


def test_chain_is_ordered_leaves_first():
    a, b, c = node("a", 0), node("b", 1), node("c", 2)
    plan = analyze({"nodes": [a, b, c], "edges": [edge(a, b), edge(b, c)]})
    subs = by_id(plan)
    assert (subs[2]["wave"], subs[1]["wave"], subs[0]["wave"]) == (0, 1, 2)
    assert plan["cycles"] == []
    assert plan["summary"]["waves"] == 3


def test_cycle_is_grouped_and_cheapest_link_is_the_cut():
    a, b = node("a", 0), node("b", 1)
    strong = [edge(a, b), edge(a, b, resolution="resolved"), edge(a, b)]
    weak = [edge(b, a, resolution="dynamic")]
    plan = analyze({"nodes": [a, b], "edges": strong + weak})
    assert len(plan["cycles"]) == 1
    cycle = plan["cycles"][0]
    assert cycle["members"] == [0, 1]
    assert [(c["from"], c["to"]) for c in cycle["cut_candidates"]] == [(1, 0)]
    subs = by_id(plan)
    assert subs[0]["readiness"] == subs[1]["readiness"] == "needs-prep"
    assert subs[0]["wave"] == subs[1]["wave"]
    assert plan["waves"][0]["units"][0]["cyclic"] is True


def test_metrics_match_hand_computation():
    a1, a2, b = node("a1", 0, "a.py"), node("a2", 0, "a.py"), node("b", 1, "b.py")
    edges = [edge(a1, a2), edge(a1, b), edge(a2, b)]
    subs = by_id(analyze({"nodes": [a1, a2, b], "edges": edges}))
    # subsystem 0: internal weight 1, efferent 2 -> cohesion 1/3, instability 1.0
    assert subs[0]["efferent"] == 2.0 and subs[0]["afferent"] == 0.0
    assert subs[0]["instability"] == 1.0
    assert subs[0]["cohesion"] == round(1 / 3, 2)
    # subsystem 1 exposes `b` to subsystem 0
    assert subs[1]["afferent"] == 2.0
    assert subs[1]["exposed_api"][0]["name"] == "b"
    assert subs[1]["exposed_api"][0]["called_from"] == [0]
    assert subs[0]["cohesion"] < 0.5 and "weak boundary" in subs[0]["blockers"][0]


def test_unassigned_nodes_do_not_create_coupling():
    a, noise = node("a", 0), node("noise", -1)
    plan = analyze({"nodes": [a, noise], "edges": [edge(a, noise), edge(noise, a)]})
    subs = by_id(plan)
    assert subs[0]["afferent"] == 0.0 and subs[0]["efferent"] == 0.0
    assert plan["summary"]["coverage"] == 0.5


def test_module_node_joins_the_subsystem_holding_most_of_its_file():
    f1, f2 = node("f1", 3, "mod.py"), node("f2", 3, "mod.py")
    module = {**node("<module>", -1, "mod.py", kind="module"), "id": "python:mod.py:<module>"}
    assert assign_subsystems([f1, f2, module])[module["id"]] == 3


def test_external_dependencies_are_profiled_not_counted_as_uncertainty():
    a = node("a", 0)
    ext = lambda pkg, kind: {"source": a["id"], "target": f"unresolved:{pkg}.call", "kind": "calls",
                             "resolution": "unresolved", "external": {"package": pkg, "kind": kind}}
    plan = analyze({"nodes": [a], "edges": [ext("fastapi", "third-party"), ext("fastapi", "third-party"), ext("json", "stdlib")]})
    sub = by_id(plan)[0]
    assert sub["third_party"] == [{"package": "fastapi", "uses": 2}]
    assert sub["stdlib"] == [{"package": "json", "uses": 1}]
    assert sub["confidence"] is None  # nothing project-internal to be uncertain about


def test_unknown_calls_only_count_when_they_resemble_project_code():
    a, helper = node("a", 0), node("helper", 0)
    unknown = lambda name: {"source": a["id"], "target": f"unresolved:{name}", "kind": "calls", "resolution": "unresolved"}
    plan = analyze({"nodes": [a, helper], "edges": [edge(a, helper), unknown("items.append"), unknown("store.helper")]})
    assert by_id(plan)[0]["confidence"] == 0.5  # 1 resolved vs 1 look-alike unknown; `append` is ignored


def test_looks_like_project_call():
    names = {"helper", "render"}
    assert looks_like_project_call("unresolved:self.helper", names)
    assert looks_like_project_call('unresolved:fetch("/x").render', names)
    assert not looks_like_project_call("unresolved:items.append", names)


def test_merged_line_count_does_not_double_count_nested_spans():
    assert merged_line_count([(1, 10), (3, 5), (11, 12), (20, 22)]) == 12 + 3


def test_reference_check_flags_invented_names_and_subsystem_numbers():
    a, b = node("alpha", 0), node("beta", 1)
    plan = analyze({"nodes": [a, b], "edges": [edge(a, b)]})
    honest = "Extract #1 first, exposing `beta`; then #0 which calls `alpha` with `0.5` confidence."
    assert check_references(honest, plan) == []
    invented = "Introduce `beta_bridge` between #0 and #7."
    assert check_references(invented, plan) == ["#7", "`beta_bridge`"]


def test_reference_check_accepts_dotted_names_made_of_known_pieces():
    a = {**node("helper", 0, "pkg/util.py"), "qualified_name": "Util.helper"}
    plan = analyze({"nodes": [a], "edges": []})
    assert check_references("see `Util.helper` and `helper()`", plan) == []


def test_empty_and_single_subsystem_graphs_do_not_crash():
    assert analyze({"nodes": [], "edges": []})["summary"]["subsystems"] == 0
    only = analyze({"nodes": [node("a", 0)], "edges": []})
    assert only["summary"]["waves"] == 1 and only["subsystems"][0]["readiness"] == "ready"


def test_prompt_stays_bounded_for_a_repo_with_dozens_of_subsystems():
    from parser.pipeline.migration_plan import MAX_PROMPT_SUBSYSTEMS, build_plan_prompt
    nodes = [node(f"fn{i}", i, f"mod{i}.py", end=10 + i) for i in range(60)]
    edges = [edge(nodes[i], nodes[i + 1]) for i in range(59)]
    plan = analyze({"nodes": nodes, "edges": edges})
    prompt = build_plan_prompt(plan)
    assert plan["summary"]["subsystems"] == 60
    assert prompt.count("\n- #") == MAX_PROMPT_SUBSYSTEMS      # one detailed line per included subsystem
    assert "smaller subsystems" in prompt
    assert len(prompt) < 12_000                                   # the 59-subsystem prompt was rejected as too large


def test_order_inside_a_cycle_follows_the_graph_once_the_weak_link_is_cut():
    a, b, c = node("a", 0), node("b", 1), node("c", 2)
    # a -> b -> c is the strong chain; c -> a (dynamic, weak) closes the cycle and is the cheapest cut.
    plan = analyze({"nodes": [a, b, c], "edges": [edge(a, b), edge(b, c), edge(c, a, resolution="dynamic")]})
    cycle = plan["cycles"][0]
    assert [(x["from"], x["to"]) for x in cycle["cut_candidates"]] == [(2, 0)]
    # with c -> a gone, c depends on nothing (first), then b, then a
    assert cycle["order_after_cuts"] == [[2], [1], [0]]
