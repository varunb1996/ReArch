"""parser/schema/graph-node.schema.json is the documented contract between the pipeline stages; it is only worth
having if something checks it. Every bundled example (Python, JavaScript, Java, C# and PHP) must satisfy it."""
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")  # a dev-only dependency; the lean hosted install does not have it

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "parser" / "schema" / "graph-node.schema.json").read_text(encoding="utf-8"))
EXAMPLES = sorted((ROOT / "demo").glob("*/graph.json"))


def test_there_are_examples_to_check():
    assert len(EXAMPLES) >= 5


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_example_graphs_satisfy_the_schema(path):
    graph = json.loads(path.read_text(encoding="utf-8"))
    errors = [e.message for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(graph)]
    assert errors == [], errors[:3]


def test_the_schema_rejects_a_graph_that_breaks_the_contract():
    broken = {"nodes": [{"id": "x", "kind": "banana", "language": "java", "path": "a", "name": "a", "qualified_name": "a"}], "edges": []}
    assert list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(broken))
