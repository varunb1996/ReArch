"""Helpers shared by the Java, C# and PHP extractors.

These three languages share a shape the Python/JS extractors do not have:
classes live in namespaces, names are declared with types (`PaymentGateway gw`),
and a file can hold several classes. The extractors reduce each file to the same
symbol-table dict the linker already consumes; `parser/pipeline/link_nominal.py`
resolves it.
"""
import re
from collections import Counter
from pathlib import Path

GENERIC_ARGS = re.compile(r"<[^<>]*>")
# Words that appear in type position but name no class we could resolve.
NON_TYPES = {"var", "auto", "dynamic", "mixed", "self", "static", "void", "object", "string", "int", "bool", "float", "array", "callable", "iterable", "null"}
# Wrappers whose type argument is what a caller actually works with (`await repo.Get()` is a Basket, not a Task).
RESULT_WRAPPERS = re.compile(r"^\s*(?:System\.Threading\.Tasks\.)?(?:Task|ValueTask|CompletableFuture|Future|Optional)\s*<(.*)>\s*$")


def text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def clean_type(raw: str | None) -> str | None:
    """`List<Item>` -> `List`, `Foo?` -> `Foo`, `\\App\\Foo` -> `App.Foo`.

    Returns None for things that are not a single nameable class: arrays,
    unions, primitives, `var`.
    """
    if not raw:
        return None
    s = raw.strip()
    while GENERIC_ARGS.search(s):
        s = GENERIC_ARGS.sub("", s)
    s = s.replace("\\", ".").replace("::", ".").strip().lstrip("?").rstrip("?").strip(".")
    # Exact match only: `String`/`Object`/`Array` are real classes; their lowercase forms are PHP/C# primitives.
    if not s or any(ch in s for ch in "|&[]() ,") or s in NON_TYPES:
        return None
    return s


def return_type(raw: str | None) -> str | None:
    """Declared return type of a method, looking through Task<T> / Optional<T> style wrappers."""
    if not raw:
        return None
    s = raw.strip()
    for _ in range(3):
        match = RESULT_WRAPPERS.match(s)
        if not match:
            break
        s = match.group(1)
    return clean_type(s)


def dotted(raw: str) -> str:
    """Namespace/qualified-name text with `\\` and `::` normalized to dots."""
    return raw.replace("\\", ".").replace("::", ".").strip().strip(".")


class SymbolTable:
    """Collects one file's facts in the shape link_calls.py expects."""

    def __init__(self, language: str, rel_path: Path):
        self.language = language
        self.path = rel_path.as_posix()
        self.module_id = f"{language}:{self.path}:<module>"
        self.namespace = ""
        self.nodes: list[dict] = []
        self.imports: list[dict] = []
        self.call_sites: list[dict] = []
        self.local_bindings: list[dict] = []
        self._seen_ids: Counter = Counter()

    def add_node(self, kind: str, name: str, qualified_name: str, ts_node, **extra) -> str:
        base = f"{self.language}:{self.path}:{qualified_name}"
        self._seen_ids[base] += 1
        count = self._seen_ids[base]
        # Overloads share a name; every one gets its own node, and calls resolve to the first.
        node_id = base if count == 1 else f"{base}~{count}"
        self.nodes.append({
            "id": node_id,
            "kind": kind,
            "language": self.language,
            "path": self.path,
            "name": name,
            "qualified_name": qualified_name,
            "start_line": ts_node.start_point[0] + 1,
            "end_line": ts_node.end_point[0] + 1,
            **extra,
        })
        return node_id

    def add_call(self, caller_id: str | None, callee_repr: str, object_repr: str | None = None,
                 property_repr: str | None = None, constructor: bool = False, kind: str | None = None) -> None:
        callee_repr = " ".join(callee_repr.split())[:120]
        object_repr = " ".join(object_repr.split())[:120] if object_repr is not None else None
        if object_repr is None:
            self.call_sites.append({
                "caller_id": caller_id,
                "callee_kind": kind or "name",
                "callee_repr": callee_repr,
                **({"constructor": True} if constructor else {}),
            })
        else:
            self.call_sites.append({
                "caller_id": caller_id,
                "callee_kind": "attribute",
                "callee_repr": callee_repr,
                "object_repr": object_repr,
                "property_repr": property_repr,
            })

    def bind(self, scope_id: str, name: str, type_repr: str | None = None, constructed: str | None = None) -> None:
        """Record what class a name holds: its declared type, or what it was `new`ed as."""
        if constructed:
            self.local_bindings.append({"scope_id": scope_id, "name": name, "value_kind": "constructor_call", "callee_repr": constructed})
        elif type_repr:
            self.local_bindings.append({"scope_id": scope_id, "name": name, "value_kind": "typed", "type_repr": type_repr})

    def bind_call_result(self, scope_id: str, name: str, object_repr: str | None, method: str) -> None:
        """`var x = recv.method(...)`: x holds whatever `method` is declared to return."""
        self.local_bindings.append({"scope_id": scope_id, "name": name, "value_kind": "call_result", "object_repr": object_repr, "method": method})

    def result(self) -> dict:
        return {
            "path": self.path,
            "language": self.language,
            "module_id": self.module_id,
            "namespace": self.namespace,
            "nodes": self.nodes,
            "imports": self.imports,
            "call_sites": self.call_sites,
            "local_bindings": self.local_bindings,
            "dict_literals": [],
            "routes": [],
        }
