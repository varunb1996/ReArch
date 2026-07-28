"""M2 (Python): walk a tree-sitter AST and extract a per-file symbol table.

Produces raw facts consumed by parser/pipeline/link_calls.py — NOT a resolved
graph yet. Kept deliberately close to the source text (no cross-file lookups
here) so this module stays testable in isolation from the rest of the pipeline.
"""
from pathlib import Path

import tree_sitter_language_pack as tslp

LANGUAGE = "python"


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _dict_pairs(dict_node, source: bytes) -> list[tuple[str, str]]:
    pairs = []
    for child in dict_node.children:
        if child.type != "pair":
            continue
        key_node, value_node = child.children[0], child.children[-1]
        pairs.append((_text(key_node, source), _text(value_node, source)))
    return pairs


def _subscript_object_and_index(node, source: bytes) -> tuple[str, str]:
    obj = node.children[0]
    index_node = None
    for i, child in enumerate(node.children):
        if child.type == "[" and i + 1 < len(node.children):
            index_node = node.children[i + 1]
            break
    return _text(obj, source), (_text(index_node, source) if index_node else "")


def extract(path: Path, rel_path: Path | None = None) -> dict:
    rel_path = rel_path or path
    source = path.read_bytes()
    parser = tslp.get_parser(LANGUAGE)
    tree = parser.parse(source)

    nodes = []
    imports = []
    call_sites = []
    local_bindings = []
    dict_literals = []
    routes = []

    module_id = f"{LANGUAGE}:{rel_path.as_posix()}:<module>"

    def walk(node, scope_stack: list[str], scope_kind_stack: list[str], current_function_id: str):
        node_type = node.type

        if node_type == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name",):
                    imports.append({"kind": "import", "module": _text(child, source), "imported_name": None, "alias": None})
                elif child.type == "aliased_import":
                    dotted = child.child_by_field_name("name")
                    alias = child.child_by_field_name("alias")
                    imports.append({
                        "kind": "import",
                        "module": _text(dotted, source) if dotted else None,
                        "imported_name": None,
                        "alias": _text(alias, source) if alias else None,
                    })

        elif node_type == "import_from_statement":
            import_kw_index = next((i for i, c in enumerate(node.children) if c.type == "import"), None)
            module_node = None
            if import_kw_index is not None:
                for c in node.children[:import_kw_index]:
                    if c.type == "dotted_name":
                        module_node = c
            module_name = _text(module_node, source) if module_node else None
            trailing = node.children[import_kw_index + 1:] if import_kw_index is not None else []
            for child in trailing:
                if child.type == "dotted_name":
                    imports.append({"kind": "from_import", "module": module_name, "imported_name": _text(child, source), "alias": None})
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    alias_node = child.child_by_field_name("alias")
                    imports.append({
                        "kind": "from_import",
                        "module": module_name,
                        "imported_name": _text(name_node, source) if name_node else None,
                        "alias": _text(alias_node, source) if alias_node else None,
                    })
                elif child.type == "wildcard_import":
                    imports.append({"kind": "from_import", "module": module_name, "imported_name": "*", "alias": None})

        elif node_type == "decorated_definition":
            inner_def = None
            collected_decorators = []
            for child in node.children:
                if child.type == "decorator":
                    call_node = next((c for c in child.children if c.type == "call"), None)
                    if call_node is None:
                        continue
                    func_field = call_node.child_by_field_name("function")
                    args_node = call_node.child_by_field_name("arguments")
                    str_args = []
                    if args_node:
                        for a in args_node.children:
                            if a.type == "string":
                                content = [gc for gc in a.children if gc.type == "string_content"]
                                str_args.append(_text(content[0], source) if content else _text(a, source))
                    if func_field is not None and str_args:
                        collected_decorators.append({"decorator_repr": _text(func_field, source), "args": str_args})
                elif child.type in ("function_definition", "class_definition"):
                    inner_def = child
            if inner_def is not None:
                name_node = inner_def.child_by_field_name("name")
                qualified_name = ".".join(scope_stack + [_text(name_node, source)]) if name_node else None
                for d in collected_decorators:
                    routes.append({"function_qualified_name": qualified_name, **d})
                walk(inner_def, scope_stack, scope_kind_stack, current_function_id)
            return

        elif node_type == "class_definition":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, source)
            qualified_name = ".".join(scope_stack + [name])
            bases = []
            superclasses = node.child_by_field_name("superclasses")
            if superclasses:
                for child in superclasses.children:
                    if child.type in ("identifier", "attribute"):
                        bases.append(_text(child, source))
            nodes.append({
                "id": f"{LANGUAGE}:{rel_path.as_posix()}:{qualified_name}",
                "kind": "class",
                "language": LANGUAGE,
                "path": rel_path.as_posix(),
                "name": name,
                "qualified_name": qualified_name,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "bases": bases,
            })
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    walk(child, scope_stack + [name], scope_kind_stack + ["class"], current_function_id)
            return

        elif node_type == "function_definition":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, source)
            qualified_name = ".".join(scope_stack + [name])
            kind = "method" if scope_kind_stack and scope_kind_stack[-1] == "class" else "function"
            func_id = f"{LANGUAGE}:{rel_path.as_posix()}:{qualified_name}"
            params_node = node.child_by_field_name("parameters")
            params = [
                _text(c, source) for c in (params_node.children if params_node else [])
                if c.type in ("identifier", "default_parameter", "typed_parameter")
            ]
            nodes.append({
                "id": func_id,
                "kind": kind,
                "language": LANGUAGE,
                "path": rel_path.as_posix(),
                "name": name,
                "qualified_name": qualified_name,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "params": params,
            })
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    walk(child, scope_stack + [name], scope_kind_stack + ["function"], func_id)
            return

        elif node_type == "call":
            func_field = node.child_by_field_name("function")
            if func_field is not None:
                if func_field.type == "identifier":
                    call_sites.append({"caller_id": current_function_id, "callee_kind": "name", "callee_repr": _text(func_field, source)})
                elif func_field.type == "attribute":
                    obj = func_field.child_by_field_name("object")
                    attr = func_field.child_by_field_name("attribute")
                    call_sites.append({
                        "caller_id": current_function_id,
                        "callee_kind": "attribute",
                        "callee_repr": _text(func_field, source),
                        "object_repr": _text(obj, source) if obj else None,
                        "property_repr": _text(attr, source) if attr else None,
                    })
                elif func_field.type == "subscript":
                    obj_repr, index_repr = _subscript_object_and_index(func_field, source)
                    call_sites.append({
                        "caller_id": current_function_id,
                        "callee_kind": "subscript",
                        "callee_repr": _text(func_field, source),
                        "object_repr": obj_repr,
                        "index_repr": index_repr,
                    })
                else:
                    call_sites.append({"caller_id": current_function_id, "callee_kind": "other", "callee_repr": _text(func_field, source)})

        elif node_type == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is not None and right is not None and left.type == "identifier":
                scope_id = current_function_id or module_id
                if right.type == "subscript":
                    obj_repr, index_repr = _subscript_object_and_index(right, source)
                    local_bindings.append({
                        "scope_id": scope_id,
                        "name": _text(left, source),
                        "value_kind": "subscript",
                        "object_repr": obj_repr,
                        "index_repr": index_repr,
                    })
                elif right.type == "dictionary":
                    dict_literals.append({
                        "scope_id": scope_id,
                        "name": _text(left, source),
                        "pairs": _dict_pairs(right, source),
                    })

        for child in node.children:
            walk(child, scope_stack, scope_kind_stack, current_function_id)

    for child in tree.root_node.children:
        walk(child, [], [], None)

    return {
        "path": rel_path.as_posix(),
        "language": LANGUAGE,
        "module_id": module_id,
        "nodes": nodes,
        "imports": imports,
        "call_sites": call_sites,
        "local_bindings": local_bindings,
        "dict_literals": dict_literals,
        "routes": routes,
    }
