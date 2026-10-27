"""Turn the bundled analyses in demo/ into the static files the GitHub Pages UI reads.

    python scripts/export_static_demo.py web/public/demo

Writes, per example, graph.json / narratives.json / migration.json /
migration.md in exactly the shapes the API would return, plus an index.json
listing the examples. Pure standard library, so a CI runner needs no pip
install. Nothing is committed: the Pages workflow regenerates it on every build.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parser.pipeline.migration_plan import render_markdown  # noqa: E402

NODE_FIELDS = ("id", "kind", "language", "path", "name", "qualified_name", "cluster")
EDGE_FIELDS = ("source", "target", "kind", "resolution", "evidence")


def api_shaped_graph(graph: dict) -> dict:
    """Same shape GraphStore serves: unresolved call targets become lightweight
    placeholder nodes (the UI hides them unless asked), and only the fields the
    UI uses are kept, which also keeps the file small."""
    nodes = [{field: node.get(field) for field in NODE_FIELDS} for node in graph["nodes"]]
    known = {node["id"] for node in nodes}
    for edge in graph["edges"]:
        for end in (edge["source"], edge["target"]):
            if end not in known:
                known.add(end)
                raw = end.split(":", 1)[-1]
                nodes.append({"id": end, "kind": "unresolved", "language": "", "path": "", "name": raw, "qualified_name": raw, "cluster": -1})
    edges = [{field: edge.get(field, "") for field in EDGE_FIELDS} for edge in graph["edges"]]
    return {"nodes": nodes, "edges": edges}


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def main(demo_dir: Path, out_dir: Path) -> list[dict]:
    index = []
    for folder in sorted(p for p in demo_dir.iterdir() if (p / "meta.json").is_file()):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        target = out_dir / folder.name
        target.mkdir(parents=True, exist_ok=True)

        write_json(target / "graph.json", api_shaped_graph(json.loads((folder / "graph.json").read_text(encoding="utf-8"))))
        narratives = folder / "narratives.json"
        write_json(target / "narratives.json", json.loads(narratives.read_text(encoding="utf-8")) if narratives.is_file() else {})
        plan = json.loads((folder / "migration.json").read_text(encoding="utf-8"))
        write_json(target / "migration.json", plan)
        (target / "migration.md").write_text(render_markdown(plan, meta["name"]), encoding="utf-8")

        index.append({
            "id": folder.name, "name": meta["name"], "git_url": meta["git_url"], "status": "ready",
            "error": None, "demo": True, "commit": meta.get("commit", ""), "analyzed_at": meta.get("analyzed_at", ""),
        })
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "index.json", index)
    return index


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) == 2 else ROOT / "web" / "public" / "demo"
    examples = main(ROOT / "demo", out)
    print(f"Exported {len(examples)} example(s) to {out}: {', '.join(e['id'] for e in examples)}")
