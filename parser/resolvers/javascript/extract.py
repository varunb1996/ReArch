"""M2 (JavaScript): walk a tree-sitter AST and extract a per-file symbol table.

Mirrors parser/resolvers/python/extract.py's output shape (nodes, imports,
call_sites, local_bindings, dict_literals, routes) so link_calls.py can treat
both languages uniformly, even though the grammars/field names differ.
"""
from pathlib import Path

import tree_sitter_language_pack as tslp

LANGUAGE = "javascript"


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _string_content(node, source: bytes) -> str | None:
    if node.type != "string":
        return None
    frag = next((c for c in node.children if c.type == "string_fragment"), None)
    return _text(frag, source) if frag else ""


def _object_pairs(obj_node, source: bytes) -> list[tuple[str, str]]:
    pairs = []
    for child in obj_node.children:
        if child.type != "pair":
            continue
        key_node = child.child_by_field_name("key")
        value_node = child.child_by_field_name("value")
        if key_node is not None and value_node is not None:
            pairs.append((_text(key_node, source), _text(value_node, source)))
    return pairs


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
            source_node = node.child_by_field_name("source")
            module_name = _string_content(source_node, source) if source_node else None
            clause = next((c for c in node.children if c.type == "import_clause"), None)
            if clause is not None:
                for part in clause.children:
                    if part.type == "identifier":
                        imports.append({"kind": "default_import", "module": module_name, "imported_name": "default", "alias": _text(part, source)})
                    elif part.type == "namespace_import":
                        alias_node = next((c for c in part.children if c.type == "identifier"), None)
                        imports.append({"kind": "namespace_import", "module": module_name, "imported_name": "*", "alias": _text(alias_node, source) if alias_node else None})
                    elif part.type == "named_imports":
                        for spec in part.children:
                            if spec.type != "import_specifier":
                                continue
                            idents = [c for c in spec.children if c.type == "identifier"]
                            name = _text(idents[0], source) if idents else None
                            alias = _text(idents[1], source) if len(idents) > 1 else None
                            imports.append({"kind": "named_import", "module": module_name, "imported_name": name, "alias": alias})

        elif node_type == "class_declaration":
            name_node = node.child_by_field_name("name")
            name = _text(name_node, source) if name_node else "<anonymous>"
            qualified_name = ".".join(scope_stack + [name])
            heritage = next((c for c in node.children if c.type == "class_heritage"), None)
            bases = []
            if heritage is not None:
                bases = [_text(c, source) for c in heritage.children if c.type == "identifier"]
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

        elif node_type in ("function_declaration", "method_definition"):
            name_node = node.child_by_field_name("name")
            name = _text(name_node, source) if name_node else "<anonymous>"
            qualified_name = ".".join(scope_stack + [name])
            kind = "method" if scope_kind_stack and scope_kind_stack[-1] == "class" else "function"
            func_id = f"{LANGUAGE}:{rel_path.as_posix()}:{qualified_name}"
            params_node = node.child_by_field_name("parameters")
            params = [_text(c, source) for c in (params_node.children if params_node else []) if c.type == "identifier"]
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

        elif node_type == "call_expression":
            func_field = node.child_by_field_name("function")
            if func_field is not None:
                if func_field.type == "identifier":
                    call_sites.append({"caller_id": current_function_id, "callee_kind": "name", "callee_repr": _text(func_field, source)})
                    args_node = node.child_by_field_name("arguments")
                    if args_node is not None:
                        str_args = [_string_content(a, source) for a in args_node.children if a.type == "string"]
                        str_args = [a for a in str_args if a is not None]
                        if str_args and _text(func_field, source) == "fetch":
                            routes.append({"function_qualified_name": None, "caller_id": current_function_id, "decorator_repr": "fetch", "args": str_args})
                elif func_field.type == "member_expression":
                    obj = func_field.child_by_field_name("object")
                    prop = func_field.child_by_field_name("property")
                    call_sites.append({
                        "caller_id": current_function_id,
                        "callee_kind": "attribute",
                        "callee_repr": _text(func_field, source),
                        "object_repr": _text(obj, source) if obj else None,
                        "property_repr": _text(prop, source) if prop else None,
                    })
                elif func_field.type == "subscript_expression":
                    obj = func_field.child_by_field_name("object")
                    idx = func_field.child_by_field_name("index")
                    call_sites.append({
                        "caller_id": current_function_id,
                        "callee_kind": "subscript",
                        "callee_repr": _text(func_field, source),
                        "object_repr": _text(obj, source) if obj else None,
                        "index_repr": _text(idx, source) if idx else None,
                    })
                else:
                    call_sites.append({"caller_id": current_function_id, "callee_kind": "other", "callee_repr": _text(func_field, source)})

        elif node_type == "new_expression":
            ctor = node.children[1] if len(node.children) > 1 else None
            if ctor is not None and ctor.type == "identifier":
                call_sites.append({"caller_id": current_function_id, "callee_kind": "name", "callee_repr": _text(ctor, source)})

        elif node_type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node is not None and value_node is not None and name_node.type == "identifier":
                scope_id = current_function_id or module_id
                if value_node.type == "subscript_expression":
                    obj = value_node.child_by_field_name("object")
                    idx = value_node.child_by_field_name("index")
                    local_bindings.append({
                        "scope_id": scope_id,
                        "name": _text(name_node, source),
                        "value_kind": "subscript",
                        "object_repr": _text(obj, source) if obj else None,
                        "index_repr": _text(idx, source) if idx else None,
                    })
                elif value_node.type == "object":
                    dict_literals.append({
                        "scope_id": scope_id,
                        "name": _text(name_node, source),
                        "pairs": _object_pairs(value_node, source),
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
