"""Cross-file resolution for Java, C# and PHP.

Python and JavaScript are linked by module path (link_calls.py). These three
languages are linked by *class name*: a file declares a namespace, a name like
`PaymentGateway` is found through imports, wildcard imports and the enclosing
namespaces, and `gateway.charge()` resolves because `gateway` was declared as a
`PaymentGateway`. Same rule as everywhere else in ReArch: an edge is "resolved"
only when one class is unambiguous; otherwise it is "unresolved" and, when the
callee root is a known library, tagged `external` so the migration plan can say
which subsystem depends on which framework.

Interface-typed receivers resolve to the interface's method, not to an
implementation: which implementation runs is a runtime (dependency-injection)
fact the parser cannot see.
"""
import re
from collections import defaultdict

NOMINAL = {"java", "csharp", "php"}
SELF_NAMES = {"this", "self", "static"}
SUPER_NAMES = {"super", "base", "parent"}
IDENT_PATH = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")

# Vendors whose reverse-domain prefix covers many unrelated libraries.
JAVA_THREE_SEGMENT = {"com.google", "org.apache", "org.eclipse", "com.fasterxml", "com.amazonaws", "io.netty"}
CSHARP_TWO_SEGMENT = {"Microsoft", "Azure", "Amazon", "Google"}

JAVA_LANG = {
    "String", "System", "Math", "Integer", "Long", "Double", "Float", "Boolean", "Character", "Byte", "Short",
    "Object", "Thread", "StringBuilder", "StringBuffer", "Exception", "RuntimeException", "Class", "Enum",
    "Runnable", "Number", "Throwable", "Error", "Iterable", "Comparable", "Record", "Void", "CharSequence",
    "AutoCloseable", "Cloneable", "Appendable", "Readable", "ThreadLocal", "Process", "ProcessBuilder", "Runtime",
    "ClassLoader", "StrictMath", "StackTraceElement", "InterruptedException", "IllegalArgumentException",
    "IllegalStateException", "NullPointerException", "UnsupportedOperationException", "IndexOutOfBoundsException",
    "ArithmeticException", "ClassCastException", "AssertionError", "CloneNotSupportedException",
    "NumberFormatException", "SecurityException", "StackOverflowError", "OutOfMemoryError", "Override",
    "Deprecated", "SuppressWarnings", "FunctionalInterface",
}
CSHARP_BUILTIN = {
    "Console", "Math", "String", "Convert", "Environment", "Guid", "DateTime", "TimeSpan", "Array",
    "Object", "Exception", "Activator", "GC", "Buffer", "Enum", "Char", "Int32", "Int64", "Double", "Decimal",
    "Boolean", "nameof", "Task", "Uri", "List", "Dictionary", "HashSet", "Queue", "Stack", "StringBuilder", "Random",
    "Stopwatch", "Tuple", "Lazy", "ArgumentException", "ArgumentNullException", "InvalidOperationException",
    "NotImplementedException", "NotSupportedException", "ArgumentOutOfRangeException", "KeyNotFoundException",
    "Func", "Action", "Nullable", "Type", "Attribute", "Version", "Path", "File", "Directory", "Regex",
}
PHP_BUILTIN_FUNCTIONS = {
    "count", "implode", "explode", "trim", "ltrim", "rtrim", "sprintf", "printf", "intval", "floatval", "isset",
    "empty", "define", "defined", "time", "microtime", "header", "var_dump", "print_r", "sleep", "usleep", "max",
    "min", "abs", "round", "floor", "ceil", "rand", "mt_rand", "uniqid", "md5", "sha1", "hash", "htmlspecialchars",
    "nl2br", "urlencode", "rawurlencode", "http_build_query", "serialize", "unserialize", "call_user_func",
    "call_user_func_array", "func_get_args", "function_exists", "class_exists", "method_exists", "property_exists",
    "get_class", "compact", "extract", "range", "usort", "uasort", "uksort", "ksort", "sort", "rsort", "in_array",
    "constant", "ini_set", "error_log", "trigger_error", "set_error_handler", "register_shutdown_function",
    "iterator_to_array", "password_hash", "password_verify", "random_int", "random_bytes", "bin2hex", "filter_var",
    "gettype", "settype", "version_compare", "phpversion", "getenv", "putenv", "setcookie", "mail", "chr", "ord",
    "lcfirst", "ucfirst", "ucwords", "wordwrap", "number_format", "levenshtein", "dirname", "basename", "realpath",
    "unlink", "mkdir", "rmdir", "touch", "copy", "rename", "glob", "scandir", "is_dir", "is_file", "sys_get_temp_dir",
    "tempnam", "ob_start", "ob_get_clean", "spl_autoload_register", "spl_object_hash", "get_object_vars",
    "get_called_class", "array_key_exists", "iterator_apply", "assert", "date", "mktime", "strtotime", "checkdate",
}
PHP_BUILTIN_PREFIXES = ("array_", "str", "preg_", "is_", "mb_", "file_", "json_", "ctype_", "curl_", "ob_", "session_", "fopen", "fclose", "fread", "fwrite")


def external_info(language: str, fqn: str) -> dict | None:
    """stdlib / third-party classification of a fully qualified name (`org.springframework.web.Foo`)."""
    parts = [p for p in fqn.split(".") if p]
    if not parts:
        return None
    if language == "java":
        if parts[0] in ("java", "javax", "jdk", "sun"):
            return {"package": ".".join(parts[:2]), "kind": "stdlib"}
        width = 3 if ".".join(parts[:2]) in JAVA_THREE_SEGMENT else 2
        return {"package": ".".join(parts[:min(width, max(len(parts) - 1, 1))]), "kind": "third-party"}
    if language == "csharp":
        if parts[0] == "System":
            return {"package": "System", "kind": "stdlib"}
        width = 2 if parts[0] in CSHARP_TWO_SEGMENT and len(parts) > 1 else 1
        return {"package": ".".join(parts[:width]), "kind": "third-party"}
    if len(parts) == 1:  # PHP: a class with no namespace is a core class (Exception, DateTime, PDO)
        return {"package": "php", "kind": "stdlib"}
    return {"package": parts[0], "kind": "third-party"}


def _builtin(language: str, name: str, bare_call: bool) -> dict | None:
    if language == "java" and name in JAVA_LANG:
        return {"package": "java.lang", "kind": "builtin"}
    if language == "csharp" and name in CSHARP_BUILTIN:
        return {"package": "System", "kind": "builtin"}
    if language == "php" and bare_call and (name in PHP_BUILTIN_FUNCTIONS or name.startswith(PHP_BUILTIN_PREFIXES)):
        return {"package": "php-builtins", "kind": "builtin"}
    return None


class NominalLinker:
    def __init__(self, tables: list[dict], node_lookup: dict[str, dict]):
        self.tables = tables
        self.node_lookup = node_lookup
        self.table_of = {t["path"]: t for t in tables}
        self.by_fqn: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.class_by_qname: dict[tuple[str, str], dict] = {}
        self.functions: dict[tuple[str, str, str], dict] = {}
        self.namespaces: dict[str, set[str]] = defaultdict(set)
        self._imports: dict[str, tuple[dict, list]] = {}
        self._bindings: dict[str, dict] = {}
        for table in tables:
            lang = table["language"]
            for n in table["nodes"]:
                if n["kind"] == "class":
                    ns = n.get("namespace") or ""
                    self.by_fqn[(lang, self.fqn(n))].append(n)
                    self.class_by_qname[(table["path"], n["qualified_name"])] = n
                    parts = ns.split(".") if ns else []
                    for i in range(1, len(parts) + 1):
                        self.namespaces[lang].add(".".join(parts[:i]))
                elif n["kind"] == "function" and "." not in n["qualified_name"]:
                    self.functions.setdefault((lang, n.get("namespace") or "", n["name"]), n)
            self._bindings[table["path"]] = {}
            for b in table["local_bindings"]:
                self._bindings[table["path"]].setdefault((b["scope_id"], b["name"]), b)

    # --- name resolution -----------------------------------------------------

    @staticmethod
    def fqn(cls: dict) -> str:
        ns = cls.get("namespace") or ""
        return f"{ns}.{cls['qualified_name']}" if ns else cls["qualified_name"]

    def imports(self, table: dict) -> tuple[dict, list]:
        cached = self._imports.get(table["path"])
        if cached is not None:
            return cached
        aliases: dict[str, str] = {}
        wildcards: list[str] = []
        for imp in table["imports"]:
            if imp["kind"] in ("type_import", "alias_import"):
                aliases[imp["alias"] or imp["imported_name"]] = imp["module"]
            elif imp["kind"] == "namespace_import":
                wildcards.append(imp["module"])
        self._imports[table["path"]] = (aliases, wildcards)
        return aliases, wildcards

    def resolve_type(self, name: str | None, table: dict, ns: str | None = None) -> dict | None:
        """The project class a (possibly dotted) type name refers to in this file, or None."""
        if not name:
            return None
        lang = table["language"]
        ns = table["namespace"] if ns is None else ns
        aliases, wildcards = self.imports(table)
        head, _, rest = name.partition(".")
        if rest and (lang, name) in self.by_fqn:  # written fully qualified
            return self.by_fqn[(lang, name)][0]
        local = self.class_by_qname.get((table["path"], name))
        if local:
            return local
        for cls in table["nodes"]:  # `Inner` written inside another class of the same file
            if cls["kind"] == "class" and cls["name"] == name:
                return cls
        if head in aliases:
            hit = self.by_fqn.get((lang, aliases[head] + (f".{rest}" if rest else "")))
            if hit:
                return hit[0]
        parts = ns.split(".") if ns else []
        # C# looks the name up in each enclosing namespace, nearest first; Java and PHP only in their own.
        depth_range = range(len(parts), -1, -1) if lang == "csharp" else [len(parts)]
        for depth in depth_range:
            prefix = ".".join(parts[:depth])
            hit = self.by_fqn.get((lang, f"{prefix}.{name}" if prefix else name))
            if hit:
                return hit[0]
        for wildcard in wildcards:
            hit = self.by_fqn.get((lang, f"{wildcard}.{name}"))
            if hit:
                return hit[0]
        return None

    def find_method(self, cls: dict, method: str, seen: set | None = None, depth: int = 0) -> str | None:
        """`method` on a class, a partial-class sibling or any resolvable base class."""
        for part in self.by_fqn.get((cls["language"], self.fqn(cls)), [cls]):
            method_id = f"{part['id']}.{method}"
            if method_id in self.node_lookup:
                return method_id
        if depth >= 8:
            return None
        seen = seen if seen is not None else set()
        seen.add(cls["id"])
        table = self.table_of[cls["path"]]
        for base in cls.get("bases", []):
            base_cls = self.resolve_type(base, table, cls.get("namespace"))
            if base_cls and base_cls["id"] not in seen:
                found = self.find_method(base_cls, method, seen, depth + 1)
                if found:
                    return found
        return None

    # --- context for one call site --------------------------------------------

    def scope_chain(self, caller: dict | None, table: dict) -> list[str]:
        """Ids whose declarations are visible from the caller: itself, its enclosing methods/classes, the file."""
        chain = []
        if caller and caller["kind"] != "module":
            parts = caller["qualified_name"].split(".")
            for i in range(len(parts), 0, -1):
                scope_id = f"{table['language']}:{table['path']}:{'.'.join(parts[:i])}"
                if scope_id in self.node_lookup:
                    chain.append(scope_id)
            if caller["id"] not in chain:
                chain.insert(0, caller["id"])
        chain.append(table["module_id"])
        return chain

    def enclosing_classes(self, caller: dict | None, table: dict) -> list[dict]:
        if not caller or caller["kind"] == "module":
            return []
        parts = caller["qualified_name"].split(".")
        found = [caller] if caller["kind"] == "class" else []  # calls in a field initializer are made by the class itself
        for i in range(len(parts) - 1, 0, -1):
            cls = self.class_by_qname.get((table["path"], ".".join(parts[:i])))
            if cls:
                found.append(cls)
        return found

    def binding_class(self, name: str, scope_ids: list[str], table: dict, ns: str, depth: int = 0) -> tuple[bool, dict | None, str | None]:
        """(has binding, class if it is a project class, declared type name)"""
        for scope_id in scope_ids:
            b = self._bindings[table["path"]].get((scope_id, name))
            if not b:
                continue
            if b["value_kind"] == "call_result":
                cls, declared = self.call_result_class(b, table, depth)
                return True, cls, declared
            declared = b.get("type_repr") or b.get("callee_repr")
            return True, self.resolve_type(declared, table, ns), declared
        return False, None, None

    def receiver_class(self, obj: str | None, scope_ids: list[str], table: dict, ns: str, enclosing: list[dict], depth: int = 0) -> dict | None:
        """The project class a receiver expression denotes: `this`, `gateway`, `this.gateway.client`, `Util`, `Strings.CS`."""
        if obj in SELF_NAMES:
            return enclosing[0] if enclosing else None
        if not obj or not IDENT_PATH.match(obj):
            return None
        parts = obj.split(".")
        if parts[0] in SELF_NAMES:
            owner, fields = (enclosing[0] if enclosing else None), parts[1:]
        else:
            has_binding, owner, _ = self.binding_class(parts[0], scope_ids, table, ns, depth)
            if has_binding:
                fields = parts[1:]
            else:  # the longest prefix that names a class; the rest are static fields (`Strings.CS`)
                owner, fields = None, []
                for i in range(len(parts), 0, -1):
                    owner = self.resolve_type(".".join(parts[:i]), table, ns)
                    if owner:
                        fields = parts[i:]
                        break
        for field in fields:
            if owner is None:
                return None
            owner = self.field_class(owner, field)
        return owner

    def field_class(self, cls: dict, field: str, seen: set | None = None, depth: int = 0) -> dict | None:
        """The project class a field of `cls` (or of a base class) is declared as."""
        for part in self.by_fqn.get((cls["language"], self.fqn(cls)), [cls]):
            b = self._bindings[part["path"]].get((part["id"], field))
            if b and b["value_kind"] != "call_result":
                return self.resolve_type(b.get("type_repr") or b.get("callee_repr"), self.table_of[part["path"]], part.get("namespace"))
        if depth >= 6:
            return None
        seen = seen if seen is not None else set()
        seen.add(cls["id"])
        for base in cls.get("bases", []):
            base_cls = self.resolve_type(base, self.table_of[cls["path"]], cls.get("namespace"))
            if base_cls and base_cls["id"] not in seen:
                found = self.field_class(base_cls, field, seen, depth + 1)
                if found:
                    return found
        return None

    def call_result_class(self, b: dict, table: dict, depth: int) -> tuple[dict | None, str | None]:
        """`var x = recv.method()`: the declared return type of `method`, when exactly one method has that name.

        Overloads can return different types, and picking one would be a guess, so a name with
        several definitions stays unresolved.
        """
        if depth >= 3:
            return None, None
        scope = self.node_lookup.get(b["scope_id"])
        enclosing = self.enclosing_classes(scope, table)
        ns = (enclosing[0].get("namespace") if enclosing else (scope or {}).get("namespace")) or table["namespace"] or ""
        scope_ids = self.scope_chain(scope, table)
        if b["object_repr"] is None:
            owner = next((c for c in enclosing if self.find_method(c, b["method"])), None)
        else:
            owner = self.receiver_class(b["object_repr"], scope_ids, table, ns, enclosing, depth + 1)
        method_id = self.find_method(owner, b["method"]) if owner else None
        method = self.node_lookup.get(method_id) if method_id else None
        if not method or f"{method_id}~2" in self.node_lookup or not method.get("returns"):
            return None, None
        declaring = self.table_of[method["path"]]
        method_class = self.class_by_qname.get((method["path"], method["qualified_name"].rpartition(".")[0]))
        return self.resolve_type(method["returns"], declaring, (method_class or method).get("namespace")), method["returns"]

    # --- external classification -----------------------------------------------

    def classify_type(self, language: str, table: dict, name: str, ns: str) -> dict | None:
        """Is a type name that is not a project class a standard-library or third-party type?"""
        aliases, wildcards = self.imports(table)
        head = name.split(".")[0]
        if self.resolve_type(name, table, ns):
            return None
        if head in aliases:
            return external_info(language, aliases[head])
        found = _builtin(language, head, bare_call=False)
        if found:
            return found
        # Declared in none of this project's namespaces and not explicitly imported: it came from one of the
        # libraries this file imports a whole namespace from. If they agree we can name it; otherwise we only
        # know it is a library type, which is still enough to stop counting it as an unresolved project call.
        outside = [w for w in wildcards if w not in self.namespaces[language]]
        if language in ("java", "csharp") and outside and head[:1].isupper():
            infos = [external_info(language, w) for w in outside]
            if len({(i["package"], i["kind"]) for i in infos if i}) == 1:
                return infos[0]
            if all(i and i["kind"] == "stdlib" for i in infos):
                return infos[0]
            return {"package": "(library)", "kind": "library"}
        return None

    def classify(self, language: str, table: dict, receiver: str | None, callee: str, scope_ids: list[str], ns: str) -> dict | None:
        aliases, _ = self.imports(table)
        subject = receiver or callee
        parts = subject.split(".")
        if receiver and parts[0] in SELF_NAMES and len(parts) > 1:
            parts = parts[1:]  # `this.gateway.charge` -> the field `gateway`
        root = parts[0]
        has_binding, project_cls, declared = self.binding_class(root, scope_ids, table, ns)
        if has_binding:
            if project_cls or not declared:
                return None
            return self.classify_type(language, table, declared, ns)
        if root in aliases:
            return None if self.resolve_type(root, table, ns) else external_info(language, aliases[root])
        found = _builtin(language, root, bare_call=receiver is None)
        if found:
            return found
        if receiver and root[:1].isupper():  # `Json.Serialize(...)`, a static call on a type the project does not define
            return self.classify_type(language, table, root, ns)
        # A library class written fully qualified at the call site (`Newtonsoft.Json.JsonConvert.Serialize`).
        # Only when every segment looks like a namespace or class, so `b.Id.ToString` on a lambda variable is left alone.
        if receiver and len(parts) >= 3 and all(IDENT_PATH.match(part) for part in parts):
            known_roots = {p.split(".")[0] for p in self.namespaces[language]}
            if language in ("csharp", "php") and parts[0] not in known_roots and all(part[:1].isupper() for part in parts[:-1]):
                return external_info(language, ".".join(parts[:-1]))
            if language == "java" and parts[0] in ("java", "javax") and parts[-2][:1].isupper():
                return external_info(language, ".".join(parts[:-1]))
        return None

    # --- edges ---------------------------------------------------------------

    def link(self) -> list[dict]:
        edges: list[dict] = []
        for table in self.tables:
            edges.extend(self.link_table(table))
        return edges

    def link_table(self, table: dict) -> list[dict]:
        lang = table["language"]
        module_id = table["module_id"]
        edges: list[dict] = []
        module_targets: set[str] = set()

        def references(node: dict, evidence: str) -> None:
            if node["id"] not in module_targets:
                module_targets.add(node["id"])
                edges.append({"source": module_id, "target": node["id"], "kind": "imports", "resolution": "resolved", "evidence": evidence})

        for imp in table["imports"]:
            if imp["kind"] == "namespace_import":
                if imp["module"] in self.namespaces[lang]:
                    continue  # calls and type references carry the real edges
                edge = {"source": module_id, "target": f"unresolved:{imp['module']}", "kind": "imports", "resolution": "unresolved"}
            else:
                hit = self.by_fqn.get((lang, imp["module"]))
                if hit:
                    references(hit[0], f"import {imp['module']}")
                    continue
                edge = {"source": module_id, "target": f"unresolved:{imp['module']}", "kind": "imports", "resolution": "unresolved"}
            external = external_info(lang, imp["module"])
            if external:
                edge["external"] = external
            edges.append(edge)

        for cls in (n for n in table["nodes"] if n["kind"] == "class"):
            ns = cls.get("namespace")
            for base in cls.get("bases", []):
                base_cls = self.resolve_type(base, table, ns)
                if base_cls:
                    edges.append({"source": cls["id"], "target": base_cls["id"], "kind": "inherits", "resolution": "resolved"})
                else:
                    edge = {"source": cls["id"], "target": f"unresolved:{base}", "kind": "inherits", "resolution": "unresolved"}
                    external = self.classify_type(lang, table, base, ns or "")
                    if external:
                        edge["external"] = external
                    edges.append(edge)

        for b in table["local_bindings"]:  # a declared type is a real dependency even if never called
            scope = self.node_lookup.get(b["scope_id"])
            ns = (scope or {}).get("namespace") or table["namespace"]
            target = self.resolve_type(b.get("type_repr") or b.get("callee_repr"), table, ns)
            if target:
                references(target, f"declared type of '{b['name']}'")

        for call in table["call_sites"]:
            caller_id = call["caller_id"] or module_id
            caller = self.node_lookup.get(caller_id)
            enclosing = self.enclosing_classes(caller, table)
            scope_ids = self.scope_chain(caller, table)
            ns = enclosing[0].get("namespace") if enclosing else ((caller or {}).get("namespace") or table["namespace"])
            ns = ns or ""
            kind = call["callee_kind"]

            def resolved(target_id: str, evidence: str | None = None) -> None:
                edge = {"source": caller_id, "target": target_id, "kind": "calls", "resolution": "resolved"}
                if evidence:
                    edge["evidence"] = evidence
                edges.append(edge)

            def unresolved(receiver: str | None) -> None:
                edge = {"source": caller_id, "target": f"unresolved:{call['callee_repr']}", "kind": "calls", "resolution": "unresolved"}
                external = self.classify(lang, table, receiver, call["callee_repr"], scope_ids, ns)
                if external:
                    edge["external"] = external
                edges.append(edge)

            if kind == "name" and call.get("constructor"):
                cls = self.resolve_type(call["callee_repr"], table, ns)
                if cls:
                    references(cls, f"new {call['callee_repr']}")
                    resolved(cls["id"], f"new {cls['qualified_name']}")
                else:
                    edge = {"source": caller_id, "target": f"unresolved:{call['callee_repr']}", "kind": "calls", "resolution": "unresolved"}
                    external = self.classify_type(lang, table, call["callee_repr"], ns)
                    if external:
                        edge["external"] = external
                    edges.append(edge)

            elif kind == "name":
                name = call["callee_repr"]
                target = None
                for cls in enclosing:
                    target = self.find_method(cls, name)
                    if target:
                        break
                if target is None and lang == "php":
                    head, _, tail = name.rpartition(".")
                    target_node = self.functions.get((lang, head, tail)) if head else (
                        self.functions.get((lang, ns, name)) or self.functions.get((lang, "", name)))
                    target = target_node["id"] if target_node else None
                if target:
                    resolved(target)
                else:
                    unresolved(None)

            elif kind == "attribute":
                obj, method = call["object_repr"], call["property_repr"]
                owner = None
                if obj in SUPER_NAMES:
                    for cls in enclosing[:1]:
                        for base in cls.get("bases", []):
                            base_cls = self.resolve_type(base, table, cls.get("namespace"))
                            if base_cls and self.find_method(base_cls, method):
                                owner = base_cls
                                break
                else:
                    owner = self.receiver_class(obj, scope_ids, table, ns, enclosing)
                target = self.find_method(owner, method) if owner else None
                if target:
                    resolved(target, f"{obj}.{method} on {owner['qualified_name']}")
                else:
                    unresolved(obj)

            else:
                unresolved(None)
        return edges
