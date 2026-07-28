# ReArch — Architecture

## Design philosophy

Two decisions shape everything else in this system:

1. **Structural graph first, LLM narrative second.** A deterministic, testable dependency/call graph is built via static analysis before any LLM reasoning happens. This is cheaper (free-tier LLM quota is scarce), lower-risk, and — critically — it makes the LLM's output more trustworthy: narratives are generated *from* verified graph facts and small targeted excerpts, never by asking a model to reason over an entire unbounded repo. If the graph is wrong, that's a debuggable, deterministic bug. If a narrative is wrong, it's flagged as inferred and shown next to the facts that produced it, so a human can judge for themselves.

2. **Free tools for every layer, with named swap-in points for scale.** Every cloud service in the original plan (Neo4j, Supabase, Hugging Face) has a genuinely free local-first stand-in that required no account signup for this MVP phase. Each stand-in is isolated behind a single file/function so swapping to the paid/cloud version later is a narrow, mechanical change — not a rewrite. See [§ Free-tier stack and swap-in points](#free-tier-stack-and-swap-in-points).

## Pipeline

The pipeline is six sequential stages, each a standalone script under `parser/pipeline/`, orchestrated end-to-end by `ingest_repo.py`. Every stage reads/writes plain JSON, so any stage can be run and inspected in isolation.

```
git clone/pull
     │
     ▼
[1] Structural parsing        parser/resolvers/{python,javascript}/extract.py
     tree-sitter AST → per-file symbol table (functions, classes, imports,
     call sites, local variable bindings, dict literals, HTTP route calls) —
     structured facts, not the AST itself. See parser/pipeline/extract_symbols.py
     for the driver that walks the repo (excluding node_modules/.venv/.git/etc,
     via parser/pipeline/repo_walk.py) and calls the right extractor per file.
     │
     ▼
[2] Cross-file/cross-language resolution   parser/pipeline/link_calls.py
     Resolves imports and call sites into a graph:
       - same-file/same-language calls (name lookup, attribute lookup on
         imported module bindings, super() → base class method)
       - Python import-root inference: tries every ancestor directory of the
         importing file, nearest first, so `from backend.db import x` inside
         a nested fixture repo resolves correctly even though the outer repo
         isn't the package root (found and fixed via real-repo testing, not
         theorized in advance — see "Real-world validation" below)
       - dynamic dispatch (dict-based fan-out, e.g. `HANDLERS[kind](...)`):
         resolved as a "dynamic" edge fanning out to every value in the dict
         literal, never collapsed to a guessed single target
       - cross-language HTTP boundaries: naive string-literal matching
         between backend route decorators/registrations and frontend
         fetch()/axios-style calls, flagged "inferred-http", never certain
     Every edge carries a `resolution` field: resolved | dynamic |
     inferred-http | unresolved. Nothing is ever asserted with false
     confidence — see parser/schema/graph-node.schema.json for the full
     node/edge schema, which every other stage conforms to.
     │
     ▼
[3] Semantic clustering       parser/pipeline/cluster_nodes.py
     Each function/method/class node is embedded from structured facts
     (qualified name, params, resolved callers/callees pulled from stage 2)
     — never raw source — using fastembed (local ONNX model, no torch, no
     account). Vectors are clustered with scikit-learn's HDBSCAN (no need to
     pick k upfront; genuinely uncategorizable nodes become noise, cluster
     -1, rather than being forced into a group). Cluster id is written back
     onto each node.
     │
     ▼
[4] LLM intent narratives     parser/pipeline/generate_narratives.py
     One narrative per cluster, not per node — keeps API volume small. The
     prompt contains: the cluster's member list, its external callers/
     callees (from stage 2's edges), and one small excerpt (~15 lines) from
     its most-connected member — read directly from the cloned source at
     the resolved start/end line. The system prompt explicitly instructs the
     model to state uncertainty about business intent rather than invent it.
     Calls Groq's OpenAI-compatible chat completions endpoint directly via
     `requests` (no SDK dependency). Results are cached by a hash of the
     cluster's sorted member-node-IDs, so re-analyzing a repo whose clusters
     haven't changed costs zero API calls — the main protection against
     Groq's free-tier rate limits. Skipped silently (not an error) if
     GROQ_API_KEY isn't set.
     │
     ▼
[5] Snapshot                  parser/pipeline/ingest_repo.py (final step)
     The resolved+clustered+narrated graph is copied to
     work_dir/snapshots/<commit_sha>.json. Because node IDs are stable
     across commits (see schema below), two snapshots can be diffed
     directly by set difference — no fuzzy matching needed.
     │
     ▼
[6] Storage + serving         api/services/graph_store.py, api/main.py
     Loaded into Kùzu (embedded graph DB) for the live UI; the JSON
     snapshots are the source of truth for diffing (stage 7, on demand).
```

**Stage 7 (on demand, not part of ingestion):** `parser/pipeline/diff_graphs.py` diffs two snapshots: nodes added/removed, edges added/removed, edges whose `resolution` changed (e.g. an edge that was `unresolved` and became `resolved` after a resolver improvement — a genuine signal, not noise), and nodes whose cluster membership moved. Exposed via `GET /api/repos/{id}/diff?from_commit=X&to_commit=Y`.

**Stage 8 (event-triggered):** `POST /api/webhooks/github` verifies GitHub's HMAC-SHA256 signature over the raw request body, matches the pushed repo's URL (normalized across `https://`/`.git` variants) against stored repos, and re-runs the entire pipeline for every user tracking that URL. This is what closes the loop from the original plan's "real-time blueprint sync via IDE/Git integration" goal.

## Data model

`parser/schema/graph-node.schema.json` is the contract every stage conforms to. The one decision everything else depends on:

**Node IDs are `<language>:<repo-relative-path>:<qualified-symbol-name>`** — never a database auto-increment ID. This is what makes diffing (stage 7) tractable: a renamed function shows up as one add + one remove of a differently-named node, not as churn across the whole graph. It's also what makes cross-stage joins trivial — the same ID means the same symbol whether you're looking at the raw graph, the clustered graph, or a narrative's "grounded_in" facts.

**Edges always carry a `resolution` field** (`resolved` / `dynamic` / `inferred-http` / `unresolved`), which is the mechanism the whole hallucination-mitigation strategy hangs off of: nothing downstream (clustering context, narrative prompts, the UI's edge styling) ever has to guess how confident a given edge is — it's explicit in the data.

## Multi-user model

`api/services/user_store.py` is a SQLite table pair (`users`, `repos`) — a zero-setup stand-in for Supabase's Postgres. Identity is a dev-only `X-User` HTTP header trusted as-is (`api/main.py`'s `get_current_user()`); there is no password or token verification. This is not real auth — it exists so the ownership-isolation logic (a user can only ever list/view their *own* repos; `GET /api/repos/{id}/graph` 404s for anyone else) could be built and demonstrated without a real auth provider. The swap-in to real auth later is exactly one function.

Each repo gets its own Kùzu DB file under `api/data/repos/<repo_id>/kuzu_db` and its own working directory (clone, symbol tables, graph snapshots, narrative cache) — full isolation at the storage layer, not just query-level filtering.

## Free-tier stack and swap-in points

| Layer | MVP choice (what's actually running) | Why | Real swap-in later |
|---|---|---|---|
| Parsing | tree-sitter + tree-sitter-language-pack | MIT, 40+ grammars, no per-language parser to hand-roll | (already the real choice — no swap needed) |
| Graph DB | **Kùzu** (embedded, single-file, Cypher-like) | Zero install, zero account — genuinely stands in for Neo4j's query model | Neo4j AuraDB Free → self-hosted Community. Confined to `api/services/graph_store.py` |
| User/repo metadata | **SQLite** | Zero setup | Supabase Postgres. Confined to `api/services/user_store.py` |
| Embeddings | **fastembed** (local ONNX, `BAAI/bge-small-en-v1.5`) | No torch, no account, no API key | Could move to a hosted embeddings API if local compute becomes the bottleneck — unlikely before real scale |
| LLM narratives | **Groq** (`llama-3.3-70b-versatile`) | Free, fast, generous enough limits for demo-scale, protected further by caching | Paid Groq tier, or add back the originally-planned Hugging Face overflow lane (explicitly skipped — HF's free tier is now thin, and caching alone already protects rate limits) |
| Backend hosting | Local (`uvicorn`) + Cloudflare quick tunnel for public reachability | Zero cost, zero deploy step for a side project at this stage | Render free tier → paid, per original plan |
| Frontend | Local Vite dev server | — | Cloudflare Pages / Vercel |
| Repo ingestion | Direct `git clone` (public repos) / PAT (private) | No OAuth app registration needed for a single-operator tool | GitHub OAuth app, once other people are connecting *their own* repos through a UI |
| Real-time sync | GitHub webhook + Cloudflare quick tunnel (ephemeral, anonymous, no account) | Free, proven to work with a real GitHub webhook delivery in testing | A stable domain (Cloudflare Tunnel with a real hostname, or just wherever the API ends up deployed) |

## Known limitations

Honest, not glossed over — these are the same three risks flagged in the original project plan, now confirmed (or refined) by building the thing:

1. **Cross-language and dynamic-language call resolution has a real ceiling.** Duck typing, reflection, DI, and true cross-service boundaries aren't visible to any static parser. The `resolution` field (dynamic/inferred-http/unresolved) exists specifically so this limitation is visible in the data rather than papered over. The Python import-root-inference heuristic (§ Pipeline, stage 2) is a real fix for a real bug found via testing against this repo's own nested fixture — but it's a heuristic (nearest-ancestor-that-resolves), not a full `sys.path`/build-system-aware resolver, and will still miss more exotic layouts (namespace packages, `PYTHONPATH` manipulation, monorepo tooling with custom resolution).

2. **LLM narratives are fundamentally under-determined.** A graph shows what calls what, not the business reason it exists. The mitigation is architectural, not just prompting: narratives are always shown next to the graph facts (`grounded_in`) that produced them, explicitly framed as inferred in the UI, and the system prompt instructs the model to describe structure rather than invent intent when the facts don't support a claim. This was validated by direct inspection of real generated narratives, which do correctly hedge ("the exact business intent is unclear without more context") rather than confabulate.

3. **Free-tier ceilings are real and were hit on purpose, not by surprise.** Groq's rate limits, a single-file Kùzu DB, and `git clone --depth 1` are all fine at demo scale (hundreds of files) and will not survive a genuine multi-hundred-thousand-line enterprise monorepo unchanged. That's an explicit, intentional boundary of this MVP phase, matching the stated plan to move to paid tiers once there's real traction — not a hidden gap.

4. **The Cloudflare quick tunnel is anonymous and ephemeral.** It has no uptime guarantee and its URL changes every time it's restarted (confirmed while building M14 — a real GitHub webhook ping was successfully delivered through it, but the webhook's Payload URL would need updating if the tunnel restarts). Fine for testing the real-time-sync feature; not something to depend on for anything long-running without either a named Cloudflare Tunnel or a real deployment.

5. **No real authentication.** The `X-User` header is trusted as-is by design, for this phase — see § Multi-user model.

## Milestone reference

The full 14-milestone build plan (with phase structure, verification approach per phase, and the original free-tier research) is preserved in this repo's development history — each milestone's implementation is one or two files listed in § Pipeline above, and was verified before moving to the next one:

- **M1–M4** (structural core): `parser/pipeline/dump_ast.py`, `parser/resolvers/*/extract.py`, `parser/pipeline/link_calls.py` — validated against `fixtures/sample_repo/GROUND_TRUTH.md`, a hand-written expected-edges document.
- **M5–M6** (storage + viewing): `api/services/graph_store.py`, `api/main.py`, `web/src/App.jsx`.
- **M7–M8** (real ingestion + multi-user): `parser/pipeline/ingest_repo.py`, `parser/pipeline/repo_walk.py`, `api/services/user_store.py`.
- **M9** (semantic layer): `parser/pipeline/cluster_nodes.py`.
- **M10–M11** (LLM narratives): `parser/pipeline/generate_narratives.py`.
- **M12** (UI polish): search/focus in `web/src/App.jsx`.
- **M13** (diffing): `parser/pipeline/diff_graphs.py`.
- **M14** (real-time sync): the `/api/webhooks/github` route in `api/main.py`.
