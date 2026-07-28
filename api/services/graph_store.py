"""M5: graph storage layer.

Uses Kuzu (embedded, Cypher-like, no server/account required) as a stand-in
for Neo4j during local dev. This is the single seam the rest of the system
talks to — swapping to real Neo4j (Aura Free or self-hosted Community) later
means rewriting this file only, per the project plan's stated migration path.
Query shapes here are intentionally plain Cypher so that swap stays cheap.
"""
import json
from pathlib import Path

import kuzu

SCHEMA_STATEMENTS = [
    """CREATE NODE TABLE IF NOT EXISTS Node(
        id STRING,
        kind STRING,
        language STRING,
        path STRING,
        name STRING,
        qualified_name STRING,
        start_line INT64,
        end_line INT64,
        cluster INT64,
        PRIMARY KEY (id)
    )""",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM Node TO Node, resolution STRING, evidence STRING)",
    "CREATE REL TABLE IF NOT EXISTS CALLS(FROM Node TO Node, resolution STRING, evidence STRING)",
    "CREATE REL TABLE IF NOT EXISTS INHERITS(FROM Node TO Node, resolution STRING, evidence STRING)",
]

EDGE_KIND_TO_REL_TABLE = {"imports": "IMPORTS", "calls": "CALLS", "inherits": "INHERITS"}

# Edge targets can be "unresolved:<raw expr>" placeholders that don't
# correspond to a real symbol anywhere in the repo. Kuzu's REL tables require
# both endpoints to exist, so these placeholders get their own lightweight
# Node rows (kind="unresolved") rather than being dropped — dropping them
# would silently hide exactly the low-confidence edges the schema exists to
# surface.
UNRESOLVED_NODE_KIND = "unresolved"


class GraphStore:
    def __init__(self, db_path: str):
        self.db = kuzu.Database(db_path)
        self.conn = kuzu.Connection(self.db)
        for stmt in SCHEMA_STATEMENTS:
            self.conn.execute(stmt)

    def load_graph_json(self, graph_json_path: str) -> None:
        graph = json.loads(Path(graph_json_path).read_text(encoding="utf-8"))
        known_ids = {n["id"] for n in graph["nodes"]}

        for node in graph["nodes"]:
            self.conn.execute(
                """MERGE (n:Node {id: $id})
                   SET n.kind=$kind, n.language=$language, n.path=$path,
                       n.name=$name, n.qualified_name=$qualified_name,
                       n.start_line=$start_line, n.end_line=$end_line,
                       n.cluster=$cluster""",
                {
                    "id": node["id"],
                    "kind": node["kind"],
                    "language": node.get("language", ""),
                    "path": node.get("path", ""),
                    "name": node.get("name", ""),
                    "qualified_name": node.get("qualified_name", ""),
                    "start_line": node.get("start_line", 0) or 0,
                    "end_line": node.get("end_line", 0) or 0,
                    "cluster": node.get("cluster", -1) if node.get("cluster") is not None else -1,
                },
            )

        seen_unresolved = set()
        for edge in graph["edges"]:
            for placeholder_id in (edge["source"], edge["target"]):
                if placeholder_id not in known_ids and placeholder_id not in seen_unresolved:
                    seen_unresolved.add(placeholder_id)
                    raw = placeholder_id.split(":", 1)[-1] if ":" in placeholder_id else placeholder_id
                    self.conn.execute(
                        """MERGE (n:Node {id: $id})
                           SET n.kind=$kind, n.name=$name, n.qualified_name=$name,
                               n.language='', n.path='', n.start_line=0, n.end_line=0,
                               n.cluster=-1""",
                        {"id": placeholder_id, "kind": UNRESOLVED_NODE_KIND, "name": raw},
                    )

        for edge in graph["edges"]:
            rel_table = EDGE_KIND_TO_REL_TABLE[edge["kind"]]
            self.conn.execute(
                f"""MATCH (a:Node {{id: $source}}), (b:Node {{id: $target}})
                    CREATE (a)-[:{rel_table} {{resolution: $resolution, evidence: $evidence}}]->(b)""",
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "resolution": edge["resolution"],
                    "evidence": edge.get("evidence") or "",
                },
            )

    def query(self, cypher: str, params: dict | None = None) -> list[list]:
        result = self.conn.execute(cypher, params or {})
        rows = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    def callers_of(self, qualified_name_substring: str) -> list[list]:
        return self.query(
            """MATCH (caller:Node)-[r:CALLS]->(callee:Node)
               WHERE callee.qualified_name CONTAINS $substr
               RETURN caller.id, r.resolution, callee.id""",
            {"substr": qualified_name_substring},
        )

    def unresolved_edges(self) -> list[list]:
        rows = []
        for rel in ("IMPORTS", "CALLS", "INHERITS"):
            rows += self.query(
                f"""MATCH (a:Node)-[r:{rel}]->(b:Node)
                    WHERE r.resolution <> 'resolved'
                    RETURN a.id, r.resolution, b.id, r.evidence"""
            )
        return rows

    def all_nodes(self) -> list[dict]:
        rows = self.query(
            """MATCH (n:Node)
               RETURN n.id, n.kind, n.language, n.path, n.name, n.qualified_name, n.cluster"""
        )
        return [
            {
                "id": r[0], "kind": r[1], "language": r[2], "path": r[3],
                "name": r[4], "qualified_name": r[5], "cluster": r[6],
            }
            for r in rows
        ]

    def all_edges(self) -> list[dict]:
        edges = []
        for rel in ("IMPORTS", "CALLS", "INHERITS"):
            rows = self.query(
                f"""MATCH (a:Node)-[r:{rel}]->(b:Node)
                    RETURN a.id, b.id, r.resolution, r.evidence"""
            )
            for source, target, resolution, evidence in rows:
                edges.append({"source": source, "target": target, "kind": rel.lower(), "resolution": resolution, "evidence": evidence})
        return edges

    def orphan_modules(self) -> list[list]:
        return self.query(
            """MATCH (m:Node {kind: 'module'})
               WHERE NOT EXISTS { MATCH (m)-[:IMPORTS]->() }
                 AND NOT EXISTS { MATCH ()-[:IMPORTS]->(m) }
               RETURN m.id"""
        )


def main(graph_json_path: str, db_path: str) -> None:
    store = GraphStore(db_path)
    store.load_graph_json(graph_json_path)

    print("=== callers of 'get_user' ===")
    for row in store.callers_of("get_user"):
        print(row)

    print("\n=== unresolved / dynamic / inferred-http edges ===")
    for row in store.unresolved_edges():
        print(row)

    print("\n=== orphan modules (no import edges in or out) ===")
    for row in store.orphan_modules():
        print(row)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: graph_store.py <graph_json_path> <db_path>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
