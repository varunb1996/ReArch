"""PHP: walk a tree-sitter AST and extract a per-file symbol table.

Facts only; parser/pipeline/link_nominal.py resolves them across files.
`$this->gateway->charge()` is recorded with receiver `this.gateway`, and the
declared type of that property (or, for untyped properties, the typed
constructor parameter assigned to it) is what lets the linker find the class.
"""
from pathlib import Path

import tree_sitter_language_pack as tslp

from parser.resolvers.common import SymbolTable, clean_type, dotted, return_type, text

LANGUAGE = "php"
CLASS_TYPES = {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"}
NAME_NODES = {"name", "qualified_name"}
PARAMETER_TYPES = {"simple_parameter", "property_promotion_parameter", "variadic_parameter"}


def _receiver(raw: str) -> str:
    """`$this->gateway` -> `this.gateway`."""
    return raw.replace("?->", ".").replace("->", ".").replace("::", ".").replace("$", "").replace("\\", ".").strip(".")


def extract(path: Path, rel_path: Path | None = None) -> dict:
    rel_path = rel_path or path
    source = path.read_bytes()
    tree = tslp.get_parser(LANGUAGE).parse(source)
    table = SymbolTable(LANGUAGE, rel_path)
    param_types: dict[str, dict[str, str]] = {}  # function id -> {param name: type}

    def add_use_clause(clause, prefix: str, kind_ok: bool) -> None:
        if not kind_ok:
            return
        name = next((c for c in clause.children if c.type in NAME_NODES), None)
        alias = clause.child_by_field_name("alias")
        if name is None:
            return
        module = dotted(f"{prefix}.{text(name, source)}" if prefix else text(name, source))
        table.imports.append({
            "kind": "type_import",
            "module": module,
            "imported_name": module.rsplit(".", 1)[-1],
            "alias": text(alias, source) if alias is not None else None,
        })

    def walk(node, scope: list[str], fn_id: str | None, class_id: str | None) -> None:
        t = node.type

        if t == "namespace_definition":
            name = node.child_by_field_name("name")
            table.namespace = dotted(text(name, source)) if name else ""
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    walk(child, scope, fn_id, class_id)
            return

        if t == "namespace_use_declaration":
            # `use function a\b;` and `use const a\B;` import no class.
            kind_ok = not any(c.type in ("function", "const") for c in node.children)
            group = node.child_by_field_name("body")
            if group is not None:
                prefix_node = next((c for c in node.children if c.type == "namespace_name"), None)
                prefix = dotted(text(prefix_node, source)) if prefix_node else ""
                for clause in group.children:
                    if clause.type == "namespace_use_clause":
                        add_use_clause(clause, prefix, kind_ok)
            else:
                for clause in node.children:
                    if clause.type == "namespace_use_clause":
                        add_use_clause(clause, "", kind_ok)
            return

        if t in CLASS_TYPES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text(name_node, source)
                qualified = ".".join(scope + [name])
                bases = []
                for child in node.children:
                    if child.type in ("base_clause", "class_interface_clause"):
                        bases += [b for b in (clean_type(text(c, source)) for c in child.children if c.type in NAME_NODES) if b]
                body = node.child_by_field_name("body")
                for child in (body.children if body else []):  # `use SomeTrait;` pulls in its methods
                    if child.type == "use_declaration":
                        bases += [b for b in (clean_type(text(c, source)) for c in child.children if c.type in NAME_NODES) if b]
                cls_id = table.add_node("class", name, qualified, node, bases=bases, namespace=table.namespace, is_interface=t == "interface_declaration")
                for child in (body.children if body else []):
                    walk(child, scope + [name], fn_id, cls_id)
                return

        if t in ("method_declaration", "function_definition"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = text(name_node, source)
                qualified = ".".join(scope + [name])
                params_node = node.child_by_field_name("parameters")
                params = [text(p, source) for p in (params_node.named_children if params_node else []) if p.type in PARAMETER_TYPES]
                is_method = t == "method_declaration" and class_id is not None
                returned = node.child_by_field_name("return_type")
                method_id = table.add_node("method" if is_method else "function", name, qualified, node, params=params,
                                           returns=return_type(text(returned, source)) if returned is not None else None,
                                           **({} if is_method else {"namespace": table.namespace}))
                param_types[method_id] = {}
                for p in (params_node.named_children if params_node else []):
                    if p.type not in PARAMETER_TYPES:
                        continue
                    p_name, p_type = p.child_by_field_name("name"), p.child_by_field_name("type")
                    declared = clean_type(text(p_type, source)) if p_type is not None else None
                    if p_name is None or not declared:
                        continue
                    bare = text(p_name, source).lstrip("$")
                    table.bind(method_id, bare, declared)
                    param_types[method_id][bare] = declared
                    if p.type == "property_promotion_parameter" and class_id:  # constructor promotion declares the property too
                        table.bind(class_id, bare, declared)
                body = node.child_by_field_name("body")
                if body is not None:
                    walk(body, scope + [name], method_id, class_id)
                return

        if t == "property_declaration" and class_id:
            type_node = node.child_by_field_name("type")
            declared = clean_type(text(type_node, source)) if type_node is not None else None
            if declared:
                for element in (c for c in node.children if c.type == "property_element"):
                    var = element.child_by_field_name("name")
                    if var is not None:
                        table.bind(class_id, text(var, source).lstrip("$"), declared)

        elif t == "assignment_expression":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is not None and right is not None:
                if left.type == "variable_name" and right.type == "object_creation_expression":
                    created = next((c for c in right.children if c.type in NAME_NODES), None)
                    constructed = clean_type(text(created, source)) if created is not None else None
                    if constructed:
                        table.bind(fn_id or table.module_id, text(left, source).lstrip("$"), constructed=constructed)
                elif left.type == "variable_name" and right.type in ("member_call_expression", "scoped_call_expression", "function_call_expression"):
                    receiver = right.child_by_field_name("object") or right.child_by_field_name("scope")  # `$x = $this->repo->find()`
                    callee = right.child_by_field_name("name") or right.child_by_field_name("function")
                    if callee is not None and callee.type in ("name", "qualified_name"):
                        table.bind_call_result(fn_id or table.module_id, text(left, source).lstrip("$"),
                                               _receiver(text(receiver, source)) if receiver is not None else None, dotted(text(callee, source)))
                elif left.type == "member_access_expression" and right.type == "variable_name" and class_id and fn_id:
                    obj, prop = left.child_by_field_name("object"), left.child_by_field_name("name")
                    if obj is not None and prop is not None and text(obj, source) == "$this":
                        assigned = param_types.get(fn_id, {}).get(text(right, source).lstrip("$"))
                        if assigned:  # `$this->gateway = $gateway;` with a typed parameter types the property
                            table.bind(class_id, text(prop, source), assigned)

        elif t == "function_call_expression":
            func = node.child_by_field_name("function")
            if func is not None:
                if func.type in NAME_NODES:
                    table.add_call(fn_id or class_id, dotted(text(func, source)))
                else:
                    table.add_call(fn_id or class_id, text(func, source), kind="other")

        elif t in ("member_call_expression", "nullsafe_member_call_expression"):
            obj, name_node = node.child_by_field_name("object"), node.child_by_field_name("name")
            if obj is not None and name_node is not None:
                prop = text(name_node, source)
                if name_node.type == "name":
                    obj_repr = _receiver(text(obj, source))
                    table.add_call(fn_id or class_id, f"{obj_repr}.{prop}", obj_repr, prop)
                else:
                    table.add_call(fn_id or class_id, text(node, source), kind="other")

        elif t == "scoped_call_expression":
            scope_node, name_node = node.child_by_field_name("scope"), node.child_by_field_name("name")
            if scope_node is not None and name_node is not None and name_node.type == "name":
                obj_repr = _receiver(text(scope_node, source))
                prop = text(name_node, source)
                table.add_call(fn_id or class_id, f"{obj_repr}.{prop}", obj_repr, prop)

        elif t == "object_creation_expression":
            created = next((c for c in node.children if c.type in NAME_NODES), None)
            constructed = clean_type(text(created, source)) if created is not None else None
            if constructed:
                table.add_call(fn_id or class_id, constructed, constructor=True)

        for child in node.children:
            walk(child, scope, fn_id, class_id)

    for child in tree.root_node.children:
        walk(child, [], None, None)
    return table.result()
