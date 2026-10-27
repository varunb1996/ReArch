"""C#: walk a tree-sitter AST and extract a per-file symbol table.

Facts only; parser/pipeline/link_nominal.py resolves them across files.
`using X;` imports a whole namespace, so the binding of a simple name to a class
is decided in the linker from namespaces, not here. Declared types of fields,
properties, parameters and locals are recorded for receiver resolution.
"""
from pathlib import Path

import tree_sitter_language_pack as tslp

from parser.resolvers.common import SymbolTable, clean_type, dotted, return_type, text

LANGUAGE = "csharp"
CLASS_TYPES = {
    "class_declaration", "interface_declaration", "struct_declaration",
    "record_declaration", "record_struct_declaration", "enum_declaration",
}
NAME_NODES = {"identifier", "qualified_name", "generic_name", "alias_qualified_name"}


def extract(path: Path, rel_path: Path | None = None) -> dict:
    rel_path = rel_path or path
    source = path.read_bytes()
    tree = tslp.get_parser(LANGUAGE).parse(source)
    table = SymbolTable(LANGUAGE, rel_path)
    namespace: list[str] = []  # block-scoped namespaces nest; a file-scoped one covers the rest of the file

    def current_namespace() -> str:
        return ".".join(namespace)

    def bind_variable_declaration(decl, scope_id: str) -> None:
        type_node = decl.child_by_field_name("type")
        declared = clean_type(text(type_node, source)) if type_node else None
        for declarator in (c for c in decl.children if c.type == "variable_declarator"):
            name_node = declarator.child_by_field_name("name")
            if name_node is None:
                continue
            constructed = None
            if declared is None:
                value = next((c for c in declarator.children if c.type not in ("identifier", "=") and c.is_named and c != name_node), None)
                if value is not None and value.type == "await_expression":
                    value = next((c for c in value.named_children), None)
                if value is not None and value.type == "object_creation_expression":  # `var x = new Foo()`
                    created_type = value.child_by_field_name("type")
                    constructed = clean_type(text(created_type, source)) if created_type else None
                elif value is not None and value.type == "invocation_expression":  # `var x = await repo.Get()`
                    func = value.child_by_field_name("function")
                    if func is not None and func.type == "identifier":
                        table.bind_call_result(scope_id, text(name_node, source), None, text(func, source))
                        continue
                    if func is not None and func.type == "member_access_expression":
                        expr, callee = func.child_by_field_name("expression"), func.child_by_field_name("name")
                        if expr is not None and callee is not None and callee.type == "identifier":
                            table.bind_call_result(scope_id, text(name_node, source), text(expr, source), text(callee, source))
                            continue
            table.bind(scope_id, text(name_node, source), declared, constructed)

    def walk(node, scope: list[str], fn_id: str | None, class_id: str | None) -> None:
        t = node.type

        if t == "using_directive":
            alias = node.child_by_field_name("name")
            target = next((c for c in node.named_children if c.type in NAME_NODES and c != alias), None)
            is_static = any(c.type == "static" for c in node.children)
            if target is None:
                return
            module = dotted(text(target, source))
            if alias is not None:
                table.imports.append({"kind": "alias_import", "module": module, "imported_name": module.rsplit(".", 1)[-1], "alias": text(alias, source)})
            elif is_static:  # `using static A.B.Util;` depends on the type A.B.Util
                table.imports.append({"kind": "type_import", "module": module, "imported_name": module.rsplit(".", 1)[-1], "alias": None})
            else:
                table.imports.append({"kind": "namespace_import", "module": module, "imported_name": None, "alias": None})
            return

        if t == "file_scoped_namespace_declaration":
            name = node.child_by_field_name("name")
            namespace[:] = dotted(text(name, source)).split(".") if name else []
            return

        if t == "namespace_declaration":
            name = node.child_by_field_name("name")
            parts = dotted(text(name, source)).split(".") if name else []
            namespace.extend(parts)
            body = node.child_by_field_name("body")
            for child in (body.children if body else []):
                walk(child, scope, fn_id, class_id)
            del namespace[len(namespace) - len(parts):]
            return

        if t in CLASS_TYPES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text(name_node, source)
                qualified = ".".join(scope + [name])
                base_list = next((c for c in node.children if c.type == "base_list"), None)
                bases = [b for b in (clean_type(text(c, source)) for c in (base_list.named_children if base_list else []) if c.type in NAME_NODES) if b]
                ns = current_namespace()
                if not table.namespace:
                    table.namespace = ns
                cls_id = table.add_node("class", name, qualified, node, bases=bases, namespace=ns, is_interface=t == "interface_declaration")
                for child in node.children:  # record primary-constructor parameters become fields
                    if child.type == "parameter_list":
                        for p in child.named_children:
                            p_name, p_type = p.child_by_field_name("name"), p.child_by_field_name("type")
                            if p_name is not None and p_type is not None:
                                table.bind(cls_id, text(p_name, source), clean_type(text(p_type, source)))
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in body.children:
                        walk(child, scope + [name], fn_id, cls_id)
                return

        if t in ("method_declaration", "constructor_declaration", "local_function_statement"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text(name_node, source)
                qualified = ".".join(scope + [name])
                params_node = node.child_by_field_name("parameters")
                params = [text(p, source) for p in (params_node.named_children if params_node else []) if p.type == "parameter"]
                is_method = bool(scope) and t != "local_function_statement" and class_id is not None
                returned = node.child_by_field_name("returns") if t == "method_declaration" else None
                method_id = table.add_node(
                    "method" if is_method else "function", name, qualified, node, params=params,
                    returns=return_type(text(returned, source)) if returned is not None else None,
                )
                if params_node is not None:
                    for p in params_node.named_children:
                        p_name, p_type = p.child_by_field_name("name"), p.child_by_field_name("type")
                        if p.type == "parameter" and p_name is not None and p_type is not None:
                            table.bind(method_id, text(p_name, source), clean_type(text(p_type, source)))
                body = node.child_by_field_name("body")
                if body is not None:
                    # a local function's own scope is not a class: calls inside resolve against the enclosing class
                    walk(body, scope + [name], method_id, class_id)
                return

        if t == "property_declaration" and class_id:
            name_node, type_node = node.child_by_field_name("name"), node.child_by_field_name("type")
            if name_node is not None and type_node is not None:
                table.bind(class_id, text(name_node, source), clean_type(text(type_node, source)))

        elif t == "variable_declaration":
            is_field = node.parent is not None and node.parent.type == "field_declaration"
            bind_variable_declaration(node, class_id if is_field and class_id else (fn_id or table.module_id))

        elif t == "invocation_expression":
            func = node.child_by_field_name("function")
            if func is not None:
                if func.type == "identifier":
                    table.add_call(fn_id or class_id, text(func, source))
                elif func.type == "generic_name":
                    ident = next((c for c in func.children if c.type == "identifier"), None)
                    if ident is not None:
                        table.add_call(fn_id or class_id, text(ident, source))
                elif func.type == "member_access_expression":
                    expr, name_node = func.child_by_field_name("expression"), func.child_by_field_name("name")
                    if name_node is not None:
                        ident = name_node if name_node.type == "identifier" else next((c for c in name_node.children if c.type == "identifier"), name_node)
                        prop = text(ident, source)
                        obj_repr = text(expr, source) if expr is not None else ""
                        if obj_repr:
                            table.add_call(fn_id or class_id, f"{obj_repr}.{prop}", obj_repr, prop)
                else:
                    table.add_call(fn_id or class_id, text(func, source), kind="other")

        elif t in ("object_creation_expression", "implicit_object_creation_expression"):
            type_node = node.child_by_field_name("type")
            created = clean_type(text(type_node, source)) if type_node else None
            if created:
                table.add_call(fn_id or class_id, created, constructor=True)

        for child in node.children:
            walk(child, scope, fn_id, class_id)

    for child in tree.root_node.children:
        walk(child, [], None, None)
    return table.result()
