"""M3: cross-file call/import resolution — the hardest logic in the pipeline.

Consumes the per-file symbol tables produced by extract_symbols.py (M2) and
produces a single graph.json conforming to parser/schema/graph-node.schema.json.

Deliberately conservative: an edge is only ever "resolved" when a single
static target is unambiguous. Anything else (dict-based dynamic dispatch,
calls on unrecognized local variables, calls into packages we can't find on
disk) is emitted as "dynamic" or "unresolved" rather than guessed — see the
schema's `resolution` field and Risk #1 in the project plan.

Usage:
    .venv/Scripts/python parser/pipeline/link_calls.py <symbols_dir> <output_graph.json>
"""
import builtins
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from parser.pipeline.link_nominal import NOMINAL, NominalLinker

PY_STDLIB = set(sys.stdlib_module_names)
PY_BUILTINS = set(dir(builtins))
NODE_CORE = {
    "fs", "path", "os", "http", "https", "url", "util", "events", "stream", "crypto",
    "child_process", "buffer", "assert", "zlib", "net", "tls", "dns", "readline", "process",
}
JS_GLOBALS = {
    "fetch", "JSON", "Promise", "document", "window", "console", "Math", "Object", "Array",
    "String", "Number", "Boolean", "Date", "Map", "Set", "RegExp", "Error", "setTimeout",
    "setInterval", "clearTimeout", "clearInterval", "parseInt", "parseFloat", "localStorage",
    "sessionStorage", "URL", "FormData", "Symbol", "BigInt", "alert", "require",
}


def external_info(language: str, raw: str) -> dict | None:
    """Classify an unresolvable import target as stdlib / third-party.

    Returns None for relative JS specifiers: those are internal files we failed
    to resolve, not external packages, and must stay plain "unresolved".
    """
    if language == "python":
        package = raw.split(".")[0]
        if not package:
            return None
        return {"package": package, "kind": "stdlib" if package in PY_STDLIB else "third-party"}
    spec = raw.split("::")[0]
    if not spec or spec.startswith("."):
        return None
    if spec.startswith("node:") or spec in NODE_CORE:
        return {"package": spec.removeprefix("node:"), "kind": "stdlib"}
    parts = spec.split("/")
    package = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
    return {"package": package, "kind": "third-party"}


def classify_external_call(language: str, callee_repr: str, root_hint: str | None, bindings: dict) -> dict | None:
    """Is an unresolved call really a call into a library / builtin?

    The root identifier of the callee (`fastapi` in `fastapi.FastAPI`, `json`
    in `json.loads`) decides it: if it is bound by a non-local import the call
    is external; if it is a language builtin/global it is builtin. Calls on
    local variables (`users.map`, `self.x`) return None and stay "unknown".
    """
    root = re.split(r"[.\(\[]", root_hint or callee_repr)[0]
    binding = bindings.get(root)
    if binding and binding["type"] == "external":
        return external_info(language, binding["raw"])
    if language == "python" and root in PY_BUILTINS:
        return {"package": "builtins", "kind": "builtin"}
    if language == "javascript" and root in JS_GLOBALS:
        return {"package": "js-globals", "kind": "builtin"}
    return None


def unresolved_call_edge(caller_id: str, call: dict, language: str, bindings: dict) -> dict:
    edge = {"source": caller_id, "target": f"unresolved:{call['callee_repr']}", "kind": "calls", "resolution": "unresolved"}
    external = classify_external_call(language, call["callee_repr"], call.get("object_repr"), bindings)
    if external:
        edge["external"] = external
    return edge


def load_symbol_tables(symbols_dir: Path) -> list[dict]:
    tables = []
    for f in sorted(symbols_dir.glob("*.symbols.json")):
        tables.append(json.loads(f.read_text(encoding="utf-8")))
    return tables


def resolve_python_module_path(dotted: str, file_index: set[str], importing_file: str = "") -> str | None:
    """Resolve a dotted Python module path against a candidate package root.

    A repo isn't always its own sys.path root (src/ layouts, nested fixture
    repos, monorepos): `from backend.db import x` inside
    `fixtures/sample_repo/backend/app.py` means the real root is
    `fixtures/sample_repo`, not the top of the repo. Try every ancestor
    directory of the importing file, nearest first, and use the first one
    where the dotted path actually resolves to a file on disk.
    """
    if not dotted:
        return None
    if dotted.startswith("."):
        return resolve_relative_python_module(dotted, file_index, importing_file)
    tail = dotted.replace(".", "/")
    ancestors = []
    parts = Path(importing_file).parent.parts if importing_file else ()
    for i in range(len(parts), -1, -1):
        ancestors.append("/".join(parts[:i]))
    for root in ancestors:
        base = f"{root}/{tail}" if root else tail
        for candidate in (f"{base}.py", f"{base}/__init__.py"):
            if candidate in file_index:
                return candidate
    return None


def resolve_relative_python_module(dotted: str, file_index: set[str], importing_file: str) -> str | None:
    """`from .x import y` (one dot: the importing file's package), `from ..x`
    (its parent package), `from . import z` (dotted is just the dots)."""
    dots = len(dotted) - len(dotted.lstrip("."))
    parts = Path(importing_file).parent.parts if importing_file else ()
    keep = len(parts) - (dots - 1)
    if keep < 0:
        return None
    root = "/".join(parts[:keep])
    tail = dotted.lstrip(".").replace(".", "/")
    candidates = [f"{root}/{tail}.py", f"{root}/{tail}/__init__.py"] if tail else [f"{root}/__init__.py"]
    return next((c for c in (c.lstrip("/") for c in candidates) if c in file_index), None)


def resolve_js_module_path(spec: str, importing_file: str, file_index: set[str]) -> str | None:
    if not spec:
        return None
    importing_dir = Path(importing_file).parent
    candidate = (importing_dir / spec).as_posix()
    parts = []
    for part in candidate.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part == ".":
            continue
        else:
            parts.append(part)
    normalized = "/".join(parts)
    return normalized if normalized in file_index else None


def build_import_bindings(table: dict, file_index: set[str]) -> dict:
    """local_name -> {"type": "module"|"symbol_ref"|"external", ...}"""
    bindings = {}
    language = table["language"]
    file_path = table["path"]

    for imp in table["imports"]:
        if language == "python":
            module_dotted = imp["module"]
            imported_name = imp["imported_name"]
            if imp["kind"] == "import":
                module_path = resolve_python_module_path(module_dotted, file_index, file_path)
                local_name = imp["alias"] or module_dotted.split(".")[-1]
                bindings[local_name] = {"type": "module", "module_path": module_path} if module_path else {"type": "external", "raw": module_dotted}
                continue
            # from_import
            local_name = imp["alias"] or imported_name
            if not module_dotted:
                submodule_dotted = imported_name
            elif module_dotted.endswith("."):  # `from . import utils`, `from .. import x`
                submodule_dotted = f"{module_dotted}{imported_name}"
            else:
                submodule_dotted = f"{module_dotted}.{imported_name}"
            submodule_path = resolve_python_module_path(submodule_dotted, file_index, file_path)
            if submodule_path:
                bindings[local_name] = {"type": "module", "module_path": submodule_path}
                continue
            module_path = resolve_python_module_path(module_dotted, file_index, file_path)
            if module_path:
                bindings[local_name] = {"type": "symbol_ref", "module_path": module_path, "symbol_name": imported_name}
            else:
                bindings[local_name] = {"type": "external", "raw": f"{module_dotted}.{imported_name}"}

        else:  # javascript
            module_spec = imp["module"]
            module_path = resolve_js_module_path(module_spec, file_path, file_index)
            local_name = imp["alias"] or imp["imported_name"]
            if imp["kind"] == "named_import" and module_path:
                bindings[local_name] = {"type": "symbol_ref", "module_path": module_path, "symbol_name": imp["imported_name"]}
            elif module_path:
                bindings[local_name] = {"type": "module", "module_path": module_path}
            else:
                bindings[local_name] = {"type": "external", "raw": f"{module_spec}::{imp['imported_name']}"}

    return bindings


def find_node_by_name_in_module(module_path: str, name: str, nodes_by_file: dict) -> dict | None:
    candidates = [n for n in nodes_by_file.get(module_path, []) if n["name"] == name and "." not in n["qualified_name"]]
    return candidates[0] if candidates else None


def resolve_local_or_import(name: str, table: dict, bindings: dict, nodes_by_file: dict) -> dict | None:
    """Resolve a bare name to a node defined in this file or imported into it."""
    local = find_node_by_name_in_module(table["path"], name, nodes_by_file)
    if local:
        return local
    binding = bindings.get(name)
    if binding and binding["type"] == "symbol_ref":
        return find_node_by_name_in_module(binding["module_path"], binding["symbol_name"], nodes_by_file)
    return None


def dynamic_fanout_targets(object_repr: str, scope_ids: list[str], table: dict, bindings: dict, nodes_by_file: dict):
    dict_lit = next((d for d in table["dict_literals"] if d["scope_id"] in scope_ids and d["name"] == object_repr), None)
    if dict_lit is None:
        return None, None
    targets = []
    for _, value_repr in dict_lit["pairs"]:
        node = resolve_local_or_import(value_repr, table, bindings, nodes_by_file)
        if node:
            targets.append(node["id"])
        else:
            targets.append(f"unresolved:{value_repr}")
    return targets, dict_lit["name"]


def find_method_id(class_node: dict, method: str, ctx: dict, nodes_by_file: dict, node_lookup: dict, seen: set | None = None, depth: int = 0) -> str | None:
    """Find `method` on a class, walking up its resolvable base classes."""
    method_id = f"{class_node['id']}.{method}"
    if method_id in node_lookup:
        return method_id
    seen = seen if seen is not None else {class_node["id"]}
    if depth >= 6:
        return None
    base_table, base_bindings = ctx[class_node["path"]]
    for base in class_node.get("bases", []):
        base_node = resolve_local_or_import(base, base_table, base_bindings, nodes_by_file)
        if base_node and base_node["kind"] == "class" and base_node["id"] not in seen:
            seen.add(base_node["id"])
            found = find_method_id(base_node, method, ctx, nodes_by_file, node_lookup, seen, depth + 1)
            if found:
                return found
    return None


def enclosing_class(caller_node: dict | None, table: dict) -> dict | None:
    if not caller_node:
        return None
    parts = caller_node["qualified_name"].split(".")
    for i in range(len(parts) - 1, 0, -1):
        qualified = ".".join(parts[:i])
        for n in table["nodes"]:
            if n["kind"] == "class" and n["qualified_name"] == qualified:
                return n
    return None


def instance_class(object_repr: str, scope_ids: list[str], table: dict, bindings: dict, nodes_by_file: dict) -> dict | None:
    """The class an identifier was constructed as (`x = Foo(...)` / `const x = new Foo()`), if known.

    Only direct constructor assignments count; anything flowing through a
    function return or a reassignment is left unresolved on purpose.
    """
    binding = next(
        (b for sid in scope_ids for b in table["local_bindings"]
         if b["scope_id"] == sid and b["name"] == object_repr and b["value_kind"] == "constructor_call"),
        None,
    )
    if not binding:
        return None
    callee = binding["callee_repr"]
    if "." in callee:
        owner, _, class_name = callee.rpartition(".")
        owner_binding = bindings.get(owner)
        node = (
            find_node_by_name_in_module(owner_binding["module_path"], class_name, nodes_by_file)
            if owner_binding and owner_binding["type"] == "module" else None
        )
    else:
        node = resolve_local_or_import(callee, table, bindings, nodes_by_file)
    return node if node and node["kind"] == "class" else None


def main(symbols_dir: str, output_path: str) -> None:
    tables = load_symbol_tables(Path(symbols_dir))

    all_nodes = []
    nodes_by_file: dict[str, list[dict]] = {}
    module_id_by_file: dict[str, str] = {}
    file_index: set[str] = set()

    for table in tables:
        file_path = table["path"]
        file_index.add(file_path)
        module_id_by_file[file_path] = table["module_id"]
        module_node = {
            "id": table["module_id"],
            "kind": "module",
            "language": table["language"],
            "path": file_path,
            "name": Path(file_path).name,
            "qualified_name": "<module>",
        }
        nodes_by_file[file_path] = list(table["nodes"])
        all_nodes.append(module_node)
        all_nodes.extend(table["nodes"])

    node_lookup = {n["id"]: n for n in all_nodes}
    # Java, C# and PHP link by class name, not module path: see link_nominal.py.
    nominal_tables = [t for t in tables if t["language"] in NOMINAL]
    module_tables = [t for t in tables if t["language"] not in NOMINAL]
    ctx = {t["path"]: (t, build_import_bindings(t, file_index)) for t in module_tables}
    edges = NominalLinker(nominal_tables, node_lookup).link() if nominal_tables else []

    for table in module_tables:
        file_path = table["path"]
        bindings = ctx[file_path][1]

        for imp in table["imports"]:
            if table["language"] == "python":
                target_mod = imp["imported_name"] if imp["kind"] == "import" else (imp["alias"] or imp["imported_name"])
            else:
                target_mod = imp["alias"] or imp["imported_name"]
            binding = bindings.get(target_mod)
            if binding is None:
                continue
            if binding["type"] == "module":
                edges.append({"source": table["module_id"], "target": module_id_by_file[binding["module_path"]], "kind": "imports", "resolution": "resolved"})
            elif binding["type"] == "symbol_ref":
                node = find_node_by_name_in_module(binding["module_path"], binding["symbol_name"], nodes_by_file)
                target_id = node["id"] if node else f"unresolved:{binding['module_path']}::{binding['symbol_name']}"
                edges.append({"source": table["module_id"], "target": target_id, "kind": "imports", "resolution": "resolved" if node else "unresolved"})
            else:
                edge = {"source": table["module_id"], "target": f"unresolved:{binding['raw']}", "kind": "imports", "resolution": "unresolved"}
                external = external_info(table["language"], binding["raw"])
                if external:
                    edge["external"] = external
                edges.append(edge)

        for cls_node in [n for n in table["nodes"] if n["kind"] == "class"]:
            for base in cls_node.get("bases", []):
                base_node = resolve_local_or_import(base, table, bindings, nodes_by_file)
                target_id = base_node["id"] if base_node else f"unresolved:{base}"
                edges.append({"source": cls_node["id"], "target": target_id, "kind": "inherits", "resolution": "resolved" if base_node else "unresolved"})

        for call in table["call_sites"]:
            caller_id = call["caller_id"] or table["module_id"]
            scope_ids = [caller_id, table["module_id"]]
            kind = call["callee_kind"]

            if kind == "name":
                name = call["callee_repr"]
                local_binding = next((b for b in table["local_bindings"] if b["scope_id"] in scope_ids and b["name"] == name and b["value_kind"] == "subscript"), None)
                if local_binding:
                    targets, dict_name = dynamic_fanout_targets(local_binding["object_repr"], scope_ids, table, bindings, nodes_by_file)
                    if targets:
                        for t in targets:
                            edges.append({"source": caller_id, "target": t, "kind": "calls", "resolution": "dynamic", "evidence": f"dict literal '{dict_name}' via local var '{name}'"})
                        continue
                node = resolve_local_or_import(name, table, bindings, nodes_by_file)
                if node:
                    edges.append({"source": caller_id, "target": node["id"], "kind": "calls", "resolution": "resolved"})
                else:
                    edges.append(unresolved_call_edge(caller_id, call, table["language"], bindings))

            elif kind == "subscript":
                targets, dict_name = dynamic_fanout_targets(call["object_repr"], scope_ids, table, bindings, nodes_by_file)
                if targets:
                    for t in targets:
                        edges.append({"source": caller_id, "target": t, "kind": "calls", "resolution": "dynamic", "evidence": f"dict literal '{dict_name}'"})
                else:
                    edges.append(unresolved_call_edge(caller_id, call, table["language"], bindings))

            elif kind == "attribute":
                object_repr = call["object_repr"]
                property_repr = call["property_repr"]
                if object_repr == "super()":
                    caller_node = node_lookup.get(caller_id)
                    class_qualified = caller_node["qualified_name"].rsplit(".", 1)[0] if caller_node else None
                    class_node = next((n for n in table["nodes"] if n["kind"] == "class" and n["qualified_name"] == class_qualified), None)
                    resolved = False
                    if class_node:
                        for base in class_node.get("bases", []):
                            base_node = resolve_local_or_import(base, table, bindings, nodes_by_file)
                            if base_node:
                                method_node = find_node_by_name_in_module(base_node["path"], property_repr, nodes_by_file) if base_node["kind"] == "module" else None
                                target_id = f"{base_node['id']}.{property_repr}"
                                if target_id in node_lookup:
                                    edges.append({"source": caller_id, "target": target_id, "kind": "calls", "resolution": "resolved", "evidence": f"super() -> base class '{base}'"})
                                    resolved = True
                    if not resolved:
                        edges.append({"source": caller_id, "target": f"unresolved:super().{property_repr}", "kind": "calls", "resolution": "unresolved"})
                    continue
                owner_class = None
                evidence = None
                if object_repr in ("self", "this"):
                    owner_class = enclosing_class(node_lookup.get(caller_id), table)
                    evidence = f"{object_repr}.{property_repr} within {owner_class['qualified_name']}" if owner_class else None
                elif object_repr and object_repr.isidentifier():
                    owner_class = instance_class(object_repr, scope_ids, table, bindings, nodes_by_file)
                    evidence = f"'{object_repr}' is constructed as {owner_class['qualified_name']}" if owner_class else None
                if owner_class:
                    method_id = find_method_id(owner_class, property_repr, ctx, nodes_by_file, node_lookup)
                    if method_id:
                        edges.append({"source": caller_id, "target": method_id, "kind": "calls", "resolution": "resolved", "evidence": evidence})
                        continue
                binding = bindings.get(object_repr)
                if binding and binding["type"] == "module":
                    node = find_node_by_name_in_module(binding["module_path"], property_repr, nodes_by_file)
                    target_id = node["id"] if node else f"unresolved:{binding['module_path']}::{property_repr}"
                    edges.append({"source": caller_id, "target": target_id, "kind": "calls", "resolution": "resolved" if node else "unresolved"})
                else:
                    edges.append(unresolved_call_edge(caller_id, call, table["language"], bindings))

            else:  # "other"
                edges.append(unresolved_call_edge(caller_id, call, table["language"], bindings))

    # M4: cross-language edges, inferred by matching HTTP route/URL string
    # literals between backend route declarations and frontend fetch() calls.
    # Never treated as certain (schema.resolution == "inferred-http") since a
    # string match is a heuristic, not a verified control-flow edge — a route
    # string could be reused for something unrelated, or built dynamically in
    # ways this pass can't see.
    python_routes: list[tuple[str, str]] = []
    js_routes: list[tuple[str, str]] = []
    for table in tables:
        for route in table.get("routes", []):
            if table["language"] == "python":
                qn = route.get("function_qualified_name")
                node = next((n for n in table["nodes"] if n["qualified_name"] == qn), None)
                if node:
                    for arg in route["args"]:
                        python_routes.append((arg, node["id"]))
            else:
                caller_id = route.get("caller_id") or table["module_id"]
                for arg in route["args"]:
                    js_routes.append((arg, caller_id))

    for path_str, js_caller in js_routes:
        for route_path, py_target in python_routes:
            if path_str == route_path:
                edges.append({
                    "source": js_caller,
                    "target": py_target,
                    "kind": "calls",
                    "resolution": "inferred-http",
                    "evidence": f"matched route string '{path_str}'",
                })

    graph = {"commit": None, "nodes": all_nodes, "edges": edges}  # no absolute paths: graphs get published
    Path(output_path).write_text(json.dumps(graph, indent=2), encoding="utf-8")
    print(f"Wrote {len(all_nodes)} nodes and {len(edges)} edges to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: link_calls.py <symbols_dir> <output_graph.json>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
