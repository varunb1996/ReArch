"""Java: walk a tree-sitter AST and extract a per-file symbol table.

Facts only, no cross-file lookups (see parser/pipeline/link_nominal.py for those).
Interfaces, enums and records are classes; constructors are methods. Declared
types of fields, parameters and locals are recorded because Java's static typing
is what lets the linker resolve `gateway.charge()` without guessing.
"""
from pathlib import Path

import tree_sitter_language_pack as tslp

from parser.resolvers.common import SymbolTable, clean_type, dotted, return_type, text

LANGUAGE = "java"
CLASS_TYPES = {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
TYPE_NODES = {"type_identifier", "scoped_type_identifier", "generic_type"}
CLAUSES = {"superclass", "super_interfaces", "extends_interfaces", "type_list"}


def _collect_types(node, source: bytes) -> list[str]:
    found = []
    for child in node.children:
        if child.type in TYPE_NODES:
            cleaned = clean_type(text(child, source))
            if cleaned:
                found.append(cleaned)
        elif child.type in CLAUSES:
            found.extend(_collect_types(child, source))
    return found


def extract(path: Path, rel_path: Path | None = None) -> dict:
    rel_path = rel_path or path
    source = path.read_bytes()
    tree = tslp.get_parser(LANGUAGE).parse(source)
    table = SymbolTable(LANGUAGE, rel_path)

    def bind_declarators(node, scope_id: str, declared: str | None) -> None:
        for declarator in node.children_by_field_name("declarator"):
            name_node = declarator.child_by_field_name("name")
            if name_node is None:
                continue
            value = declarator.child_by_field_name("value")
            constructed = None
            if declared is None and value is not None and value.type == "object_creation_expression":  # `var x = new Foo()`
                type_node = value.child_by_field_name("type")
                constructed = clean_type(text(type_node, source)) if type_node else None
            if declared is None and constructed is None and value is not None and value.type == "method_invocation":  # `var x = repo.find()`
                callee, receiver = value.child_by_field_name("name"), value.child_by_field_name("object")
                if callee is not None:
                    table.bind_call_result(scope_id, text(name_node, source), text(receiver, source) if receiver is not None else None, text(callee, source))
                continue
            table.bind(scope_id, text(name_node, source), declared, constructed)

    def walk(node, scope: list[str], fn_id: str | None, class_id: str | None) -> None:
        t = node.type

        if t == "package_declaration":
            name = next((c for c in node.children if c.type in ("scoped_identifier", "identifier")), None)
            table.namespace = dotted(text(name, source)) if name else ""
            return

        if t == "import_declaration":
            name = next((c for c in node.children if c.type in ("scoped_identifier", "identifier")), None)
            if name is None:
                return
            module = dotted(text(name, source))
            wildcard = any(c.type == "asterisk" for c in node.children)
            is_static = any(c.type == "static" for c in node.children)
            if is_static and not wildcard:  # `import static a.b.Foo.bar;` depends on class a.b.Foo
                module = module.rpartition(".")[0]
                wildcard = False
            if wildcard and not is_static:
                table.imports.append({"kind": "namespace_import", "module": module, "imported_name": None, "alias": None})
            elif module:
                table.imports.append({"kind": "type_import", "module": module, "imported_name": module.rsplit(".", 1)[-1], "alias": None})
            return

        if t in CLASS_TYPES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text(name_node, source)
                qualified = ".".join(scope + [name])
                bases = _collect_types(node, source)
                cls_id = table.add_node(
                    "class", name, qualified, node,
                    bases=bases, namespace=table.namespace, is_interface=t == "interface_declaration",
                )
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        walk(child, scope + [name], fn_id, cls_id)
                return

        if t in ("method_declaration", "constructor_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text(name_node, source)
                qualified = ".".join(scope + [name])
                params_node = node.child_by_field_name("parameters")
                params = [text(p, source) for p in (params_node.named_children if params_node else []) if p.type in ("formal_parameter", "spread_parameter")]
                returned = node.child_by_field_name("type") if node.type == "method_declaration" else None
                method_id = table.add_node(
                    "method" if scope else "function", name, qualified, node, params=params,
                    returns=return_type(text(returned, source)) if returned is not None else None,
                )
                if params_node is not None:
                    for p in params_node.named_children:
                        p_name = p.child_by_field_name("name")
                        p_type = p.child_by_field_name("type")
                        if p.type == "formal_parameter" and p_name is not None and p_type is not None:
                            table.bind(method_id, text(p_name, source), clean_type(text(p_type, source)))
                body = node.child_by_field_name("body")
                if body is not None:
                    walk(body, scope + [name], method_id, class_id)
                return

        if t == "field_declaration" and class_id:
            type_node = node.child_by_field_name("type")
            bind_declarators(node, class_id, clean_type(text(type_node, source)) if type_node else None)

        elif t == "local_variable_declaration":
            type_node = node.child_by_field_name("type")
            declared = clean_type(text(type_node, source)) if type_node else None
            bind_declarators(node, fn_id or table.module_id, declared)

        elif t == "method_invocation":
            name_node = node.child_by_field_name("name")
            obj = node.child_by_field_name("object")
            if name_node is not None:
                name = text(name_node, source)
                if obj is None:
                    table.add_call(fn_id or class_id, name)
                else:
                    obj_repr = text(obj, source)
                    table.add_call(fn_id or class_id, f"{obj_repr}.{name}", obj_repr, name)

        elif t == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            created = clean_type(text(type_node, source)) if type_node else None
            if created:
                table.add_call(fn_id or class_id, created, constructor=True)

        for child in node.children:
            walk(child, scope, fn_id, class_id)

    for child in tree.root_node.children:
        walk(child, [], None, None)
    return table.result()
