"""End-to-end check of extract -> link against the hand-written fixture.

fixtures/sample_repo/GROUND_TRUTH.md is the human-written source of truth for
what the linker must find; these tests are that document made executable, so a
resolver change that silently breaks a known edge fails here instead of in a
demo.
"""
import json
from pathlib import Path

import pytest

from parser.pipeline.extract_symbols import main as extract_symbols
from parser.pipeline.link_calls import main as link_calls

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    work = tmp_path_factory.mktemp("fixture")
    extract_symbols(str(FIXTURE), str(work / "symbols"))
    link_calls(str(work / "symbols"), str(work / "graph.json"))
    return json.loads((work / "graph.json").read_text(encoding="utf-8"))


def edges(graph, kind=None, resolution=None):
    return {
        (e["source"], e["target"])
        for e in graph["edges"]
        if (kind is None or e["kind"] == kind) and (resolution is None or e["resolution"] == resolution)
    }


def test_graph_size_is_stable(graph):
    assert len(graph["nodes"]) == 46
    assert len(graph["edges"]) == 56


@pytest.mark.parametrize("source,target", [
    ("python:backend/app.py:list_users", "python:backend/services/user_service.py:get_all_users"),
    ("python:backend/app.py:create_user_route", "python:backend/services/user_service.py:create_user"),
    ("python:backend/app.py:notify_route", "python:backend/dispatch.py:dispatch"),
    ("python:backend/services/user_service.py:create_user", "python:backend/db.py:save_user"),
    ("python:backend/services/user_service.py:get_user_by_id", "python:backend/db.py:get_user"),
    ("javascript:frontend/components/UserList.js:loadUsers", "javascript:frontend/api.js:fetchUsers"),
    ("javascript:frontend/components/NotifyButton.js:NotifyButton.onClick", "javascript:frontend/api.js:sendNotification"),
])
def test_cross_file_calls_resolve(graph, source, target):
    assert (source, target) in edges(graph, "calls", "resolved")


def test_super_call_resolves_to_base_method(graph):
    assert (
        "python:backend/models.py:Admin.describe",
        "python:backend/models.py:User.describe",
    ) in edges(graph, "calls", "resolved")


def test_inheritance_edge(graph):
    assert ("python:backend/models.py:Admin", "python:backend/models.py:User") in edges(graph, "inherits", "resolved")


def test_constructor_assigned_instance_methods_resolve(graph):
    resolved = edges(graph, "calls", "resolved")
    module = "javascript:frontend/index.js:<module>"
    assert (module, "javascript:frontend/components/UserList.js:UserList.render") in resolved
    assert (module, "javascript:frontend/components/NotifyButton.js:NotifyButton.onClick") in resolved


def test_dict_dispatch_fans_out_as_dynamic_never_resolved(graph):
    dynamic = edges(graph, "calls", "dynamic")
    source = "python:backend/dispatch.py:dispatch"
    assert (source, "python:backend/services/notification_service.py:send_email") in dynamic
    assert (source, "python:backend/services/notification_service.py:send_sms") in dynamic
    assert not any(s == source for s, _ in edges(graph, "calls", "resolved"))


def test_frontend_fetch_matches_backend_routes_as_inferred_http(graph):
    inferred = edges(graph, "calls", "inferred-http")
    assert ("javascript:frontend/api.js:fetchUsers", "python:backend/app.py:list_users") in inferred
    assert ("javascript:frontend/api.js:createUser", "python:backend/app.py:create_user_route") in inferred
    assert ("javascript:frontend/api.js:sendNotification", "python:backend/app.py:notify_route") in inferred


def test_builtin_calls_are_labelled_external_not_unknown(graph):
    fetch_edges = [e for e in graph["edges"] if e["target"] == "unresolved:fetch"]
    assert fetch_edges
    assert all(e.get("external", {}).get("kind") == "builtin" for e in fetch_edges)
