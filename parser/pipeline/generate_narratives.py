"""M10/M11: LLM intent-narrative generation, grounded in graph facts + a
small excerpt per cluster — never the whole file/repo.

Per the project plan's Risk #2 (hallucination in intent inference):
narratives are explicitly framed as inferred, not asserted, and the grounded
facts (calls out/in, which excerpt was shown) are stored alongside the text
so a user can judge for themselves rather than trusting an oracle.

Caching is keyed by cluster composition (sorted member node IDs), so
re-analyzing a repo whose clusters haven't changed costs zero API calls —
this is the main protection against Groq's free-tier rate limits.

Usage:
    .venv/Scripts/python parser/pipeline/generate_narratives.py <graph.json> <repo_dir> <output.json> [cache.json]
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retires models without much notice (llama-3.3-70b-versatile disappeared
# from the free tier). Override with GROQ_MODEL in .env if this one goes too;
# GET https://api.groq.com/openai/v1/models lists what a key can use.
DEFAULT_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "You analyze subsystems of a codebase discovered by clustering its call graph. "
    "You are given only structural facts (which functions call which, cluster membership) "
    "plus one short code excerpt — never the whole codebase. Write 2-4 concise sentences "
    "explaining what the subsystem likely does and why it exists as a cohesive unit. "
    "Be explicit about uncertainty: if the facts don't support a claim about business intent, "
    "describe what the code does structurally instead of guessing why. Do not invent details "
    "not supported by the facts given."
)


def read_excerpt(repo_dir: Path, path: str, start_line: int | None, end_line: int | None, max_lines: int = 15) -> str:
    file_path = repo_dir / path
    if not path or not file_path.is_file() or not file_path.resolve().is_relative_to(repo_dir.resolve()):
        return ""
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, (start_line or 1) - 1)
    end = min(len(lines), start + max_lines, end_line or start + max_lines)
    return "\n".join(lines[start:end])


def build_cluster_prompt(cluster_id: int, members: list[dict], calls_out: list[str], calls_in: list[str], excerpt_node: dict | None, excerpt_text: str) -> str:
    member_lines = "\n".join(f"- {m['qualified_name']} ({m['kind']}) in {m['path']}" for m in members)
    out_lines = "\n".join(f"- {c}" for c in sorted(set(calls_out))[:15]) or "(none detected)"
    in_lines = "\n".join(f"- {c}" for c in sorted(set(calls_in))[:15]) or "(none detected)"
    excerpt_block = (
        f"Representative excerpt from {excerpt_node['qualified_name']} ({excerpt_node['path']}):\n```\n{excerpt_text}\n```"
        if excerpt_node and excerpt_text
        else "(no excerpt available)"
    )
    return (
        f"Subsystem #{cluster_id} — {len(members)} members:\n{member_lines}\n\n"
        f"Calls out to (external dependencies):\n{out_lines}\n\n"
        f"Called by (external dependents):\n{in_lines}\n\n"
        f"{excerpt_block}\n\n"
        "Explain what this subsystem likely does and why it exists as a cohesive unit."
    )


def cluster_cache_key(cluster_id: int, members: list[dict]) -> str:
    basis = str(cluster_id) + "|" + "|".join(sorted(m["id"] for m in members))
    return hashlib.sha256(basis.encode()).hexdigest()


def call_groq(prompt: str, api_key: str, model: str, system: str = SYSTEM_PROMPT, max_tokens: int = 220) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if "gpt-oss" in model:
        # Reasoning models spend completion tokens thinking before they answer;
        # keep that small and don't return the thinking text.
        payload["reasoning_effort"] = "low"
        payload["include_reasoning"] = False
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for attempt in range(5):
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 429 and attempt < 4:
            # Free tier allows ~8K tokens a minute; a burst of calls can exhaust it. Wait as
            # long as Groq asks (a full window can be ~60s), then retry.
            time.sleep(min(float(resp.headers.get("retry-after", 10)), 60))
            continue
        resp.raise_for_status()
        content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        if not content:
            raise RuntimeError("Groq returned an empty answer (token budget spent on reasoning?)")
        return content.translate(TYPOGRAPHY_TO_ASCII)
    raise RuntimeError("Groq request failed after retries")


# gpt-oss likes typographic spaces and hyphens (narrow no-break space,
# no-break space, non-breaking hyphen, hyphen). They show up as mojibake in
# terminals and break copy-paste, so flatten them to plain ASCII.
TYPOGRAPHY_TO_ASCII = str.maketrans({chr(0x202F): " ", chr(0x00A0): " ", chr(0x2011): "-", chr(0x2010): "-"})

FAILURE_MARKER = "(narrative generation failed"
# Subsystem ids are numbered biggest-first, so on a large repo the largest ones get
# narratives and the long tail does not cost an LLM call each.
MAX_NARRATED_CLUSTERS = 20


def main(graph_path: str, repo_dir: str, output_path: str, cache_path: str | None = None) -> None:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("GROQ_API_KEY not set; skipping narrative generation (this is optional).")
        Path(output_path).write_text(json.dumps({}), encoding="utf-8")
        return

    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    repo_path = Path(repo_dir)

    cache: dict = {}
    cache_file = Path(cache_path) if cache_path else None
    if cache_file and cache_file.exists():
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        # Older versions cached error strings as if they were narratives. Drop
        # them so those clusters are retried instead of showing an error forever.
        cache = {k: v for k, v in cache.items() if not v["text"].startswith(FAILURE_MARKER)}

    nodes_by_id = {n["id"]: n for n in graph["nodes"]}
    by_cluster: dict[int, list[dict]] = {}
    for n in graph["nodes"]:
        if n.get("cluster", -1) >= 0 and n["kind"] in ("function", "method", "class"):
            by_cluster.setdefault(n["cluster"], []).append(n)

    edges_by_source: dict[str, list[dict]] = {}
    edges_by_target: dict[str, list[dict]] = {}
    for e in graph["edges"]:
        if e["kind"] != "calls":
            continue
        edges_by_source.setdefault(e["source"], []).append(e)
        edges_by_target.setdefault(e["target"], []).append(e)

    narratives: dict = {}
    calls_made = 0
    failed = 0

    for cluster_id, members in sorted(by_cluster.items())[:MAX_NARRATED_CLUSTERS]:
        member_ids = {m["id"] for m in members}
        key = cluster_cache_key(cluster_id, members)

        if key in cache:
            narratives[str(cluster_id)] = cache[key]
            continue

        calls_out, calls_in = [], []
        for m in members:
            for e in edges_by_source.get(m["id"], []):
                if e["target"] not in member_ids:
                    target = nodes_by_id.get(e["target"])
                    calls_out.append(target["qualified_name"] if target else e["target"])
            for e in edges_by_target.get(m["id"], []):
                if e["source"] not in member_ids:
                    source = nodes_by_id.get(e["source"])
                    calls_in.append(source["qualified_name"] if source else e["source"])

        excerpt_node = max(
            members,
            key=lambda m: len(edges_by_source.get(m["id"], [])) + len(edges_by_target.get(m["id"], [])),
        )
        excerpt_text = read_excerpt(repo_path, excerpt_node["path"], excerpt_node.get("start_line"), excerpt_node.get("end_line"))

        prompt = build_cluster_prompt(cluster_id, members, calls_out, calls_in, excerpt_node, excerpt_text)

        try:
            text = call_groq(prompt, api_key, model, max_tokens=900)
        except Exception as exc:
            # Leave this cluster without a narrative (and out of the cache) so
            # the next run retries it, rather than storing the error as an answer.
            print(f"  cluster {cluster_id}: narrative failed, will retry next run ({exc})")
            failed += 1
            continue

        entry = {
            "text": text,
            "member_count": len(members),
            "grounded_in": {
                "calls_out": sorted(set(calls_out))[:15],
                "calls_in": sorted(set(calls_in))[:15],
                "excerpt_source": f"{excerpt_node['qualified_name']} ({excerpt_node['path']})",
            },
        }
        narratives[str(cluster_id)] = entry
        cache[key] = entry
        calls_made += 1

    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    Path(output_path).write_text(json.dumps(narratives, indent=2), encoding="utf-8")
    cached = len(narratives) - calls_made
    print(f"Generated {calls_made} new narratives ({cached} from cache, {failed} failed) for {len(by_cluster)} clusters.")


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print("Usage: generate_narratives.py <graph.json> <repo_dir> <output.json> [cache.json]")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) == 5 else None)
