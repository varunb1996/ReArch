"""Relative imports are how real Python packages import each other. An early
version didn't recognize them at all: every intra-package import was recorded
as an unresolved external package named "None"."""
import json

from parser.pipeline.extract_symbols import main as extract_symbols
from parser.pipeline.link_calls import main as link_calls


def build(tmp_path, files):
    repo = tmp_path / "repo"
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    extract_symbols(str(repo), str(tmp_path / "symbols"))
    link_calls(str(tmp_path / "symbols"), str(tmp_path / "graph.json"))
    return json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))


def resolved(graph, kind):
    return {(e["source"], e["target"]) for e in graph["edges"] if e["kind"] == kind and e["resolution"] == "resolved"}


def test_dot_dotdot_and_from_dot_import_module_all_resolve(tmp_path):
    graph = build(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/util.py": "def helper():\n    pass\n",
        "pkg/core.py": "from . import util\nfrom .util import helper\n\ndef run():\n    util.helper()\n    helper()\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/deep.py": "from ..util import helper\n\ndef go():\n    helper()\n",
    })
    calls = resolved(graph, "calls")
    helper = "python:pkg/util.py:helper"
    assert ("python:pkg/core.py:run", helper) in calls
    assert ("python:pkg/sub/deep.py:go", helper) in calls

    imports = resolved(graph, "imports")
    assert ("python:pkg/core.py:<module>", "python:pkg/util.py:<module>") in imports   # from . import util
    assert ("python:pkg/core.py:<module>", helper) in imports                           # from .util import helper
    assert ("python:pkg/sub/deep.py:<module>", helper) in imports                       # from ..util import helper


def test_unresolvable_relative_import_is_not_reported_as_a_third_party_package(tmp_path):
    graph = build(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": "from .missing import thing\n\ndef f():\n    thing()\n"})
    assert not any("None" in e["target"] for e in graph["edges"])
    assert not any(e.get("external", {}).get("kind") == "third-party" for e in graph["edges"])
