# ReArch — Architecture

## Design principles

1. **Measure first, narrate second.** Everything structural is computed deterministically: the dependency graph, the subsystems, the coupling metrics, the extraction order. The LLM only turns measured facts into prose. It is never asked to discover structure, never sees whole files, and its output is machine-checked against the facts (see [the reference guard](#5-migration-plan)).
2. **Never assert more than you know.** Every edge carries a `resolution` (`resolved`, `dynamic`, `inferred-http`, `unresolved`). Unresolved calls carry no coupling weight and lower a subsystem's confidence score instead of being guessed at. Narratives are shown beside the facts that produced them.
3. **Free first, with narrow swap points.** Each cloud service in the original plan has a free local stand-in isolated behind one file (Kùzu for Neo4j, SQLite for Postgres), so moving to the paid service later is a mechanical change.

## Pipeline

Each stage is a script under `parser/pipeline/` that reads and writes plain JSON, so any stage can be run and inspected on its own. `ingest_repo.py` runs them in order.

```
git clone (hardened)  ->  1 parse  ->  2 link  ->  3 cluster  ->  4 narrate  ->  5 migration plan  ->  6 snapshot
```

### 1. Parse — `parser/resolvers/{python,javascript}/extract.py`
tree-sitter parses each file; the extractor reduces the AST to a per-file symbol table: functions/classes/methods with line ranges, imports, call sites, local `x = Foo()` constructions, dict literals (for dispatch tables) and HTTP routes/`fetch` calls. `repo_walk.py` decides which files are in scope: it skips vendor and build directories, **test code** (tests describe how a system is checked, not how it is built; on `psf/requests` they were 60% of all symbols), minified or oversized files, symlinks, and anything listed in the repo's `.rearchignore`.

### 2. Link — `link_calls.py`
Resolves the symbol tables into a graph:
- imports and direct calls, across files, with Python package-root inference (tries every ancestor of the importing file, nearest first);
- `self.m()` / `this.m()` and `x = Foo(); x.m()` (direct constructor assignments only), following inheritance;
- dict-based dispatch as a `dynamic` fan-out to every value in the table, never collapsed to a guessed target;
- frontend `fetch("/api/x")` matched to backend route strings as `inferred-http`;
- **external dependencies**: an unresolved call whose root name is bound to a non-project import is tagged `stdlib`, `third-party` (with the package name) or `builtin`. This is what lets the migration plan say which subsystem is tied to which framework.

Calls on values the analysis cannot type (`items.append`, `node.child_by_field_name`) stay plain `unresolved`.

### 3. Cluster — `cluster_nodes.py`
Each function/method/class is embedded from structured facts (qualified name, parameters, resolved callers and callees, never raw source) with `fastembed` (`bge-small-en-v1.5`, local ONNX). Then:
1. **HDBSCAN** finds tight cores (no `k` to choose, genuine outliers stay unassigned).
2. **Merge to a target count**, `max(5, round(0.7·√n))` subsystems, folding the smallest cluster into its nearest neighbour. HDBSCAN alone reported 59 "subsystems" for a 37-file library; a plan with dozens of subsystems is noise.
3. **Adopt unassigned nodes** into the subsystem they mostly call and are called by (strictly more than half of their resolved neighbours, so a node that straddles two subsystems stays unassigned).
4. **Keep structure whole**: a class and its methods, a function and its nested functions, always share one subsystem. Embeddings otherwise split them, which fakes coupling.
5. Renumber so `#0` is the largest.

### 4. Narrate — `generate_narratives.py`
One narrative per subsystem (the largest 20), prompted with its members, external callers/callees and one ~15-line excerpt from its most-connected member. Cached by the subsystem's member ids, so re-analysis of an unchanged subsystem costs nothing. Failures are never cached or stored as text; the subsystem is simply retried next run. Model: `openai/gpt-oss-120b` on Groq (override with `GROQ_MODEL`; Groq retires models without notice).

### 5. Migration plan — `migration_plan.py`
Deterministic analysis over the clustered graph. Constants are named at the top of the file so every label is explainable and tunable.

| Measure | Definition |
|---|---|
| Coupling weight of an edge | resolution (`resolved` 1.0, `inferred-http` 0.75, `dynamic` 0.5, unresolved 0) × kind (`inherits` 1.5, `calls` 1.0, `imports` 0.5) |
| Cohesion | internal weight / (internal + incoming + outgoing). Below 50% the cluster is not a clean boundary. |
| Instability | outgoing / (incoming + outgoing), Martin's metric. |
| Confidence | resolved edges / (resolved + dynamic/inferred + unresolved calls whose method name matches something this project defines). Calls like `items.append` are not counted: they cannot be project code. |
| Waves | Tarjan SCCs condense dependency cycles; wave *k* depends only on waves < *k*; wave 0 depends on nothing and is extracted first. |
| Cycle cut candidates | greedy feedback-arc-set: repeatedly remove the lowest-weight link still inside a cycle. A starting point for where to introduce an interface, not a proof of the minimum cut. |
| Seams | nodes in a subsystem that other subsystems call, ranked by incoming weight: the interface to introduce when extracting it. |
| Readiness | `needs-prep` if in a cycle or cohesion < 50%; else `caution` if confidence < 60%, dynamic dispatch is present, the public surface exceeds 8 entry points, 4+ third-party packages are used or it crosses an HTTP boundary; else `ready`. Reasons are always listed. |

The written plan is one LLM call over a compact rendering of these facts (the 15 largest or cyclic subsystems when there are more; a 59-subsystem prompt was rejected by Groq as too large). **Reference guard:** every backticked identifier and `#<id>` in the result must exist in the measured facts. A violation triggers one corrective retry; anything still unverified is stored as `narrative_warnings` and shown to the user. This caught real inventions (made-up function names, a wrong subsystem label) that prompting alone did not prevent.

### 6. Snapshot and store
The graph is copied to `snapshots/<commit>.json` (node ids are `language:path:qualified_name`, stable across commits, so diffing is plain set difference: `diff_graphs.py`) and loaded into Kùzu for the live UI.

## Running it in public

`REARCH_PUBLIC_MODE=1` (set in the Dockerfile) turns on `api/security.py`. Local development stays permissive.

- **Repo URLs**: only `https://github.com/<owner>/<repo>`. Always, even locally, anything git could read as an option (`--upload-pack=...`) is refused, and the clone uses `--`, `core.symlinks=false`, a timeout, no credential prompts, `--depth 1`. Without these, a stranger could clone the server's own directories or run commands through git options.
- **Identity**: the `X-User` header is a bearer secret. In public mode it must be a long random id (the browser generates one); short guessable names are refused, so nobody can read another visitor's analyses by guessing a username. A reserved read-only `demo` account holds the bundled examples.
- **Isolation**: all reads go through one `require_repo` check; demo repos are readable by anyone and writable by no one. Commit ids in diff URLs are validated so they cannot escape the snapshots folder.
- **Load**: analyses run as background jobs (the request returns `pending` and the UI polls, because they outlive a hosting proxy's request timeout); one at a time; 3 per visitor per hour and 10 per IP per day; repository size capped; unexpected errors are logged server-side and replaced with a generic message so server paths never reach users.
- **Examples**: `demo/<repo>/` holds pre-computed results (`scripts/build_demo.py`). A fresh server loads them in seconds with no clone and no LLM call, which matters on an ephemeral disk.

## Deployment topology

```
Browser ──> GitHub Pages (static React build)
   │
   └─────> Hugging Face Space (Docker: FastAPI + Kùzu + SQLite, ephemeral disk)
                 ├─> git clone  github.com (public repos)
                 └─> Groq API   (narratives + plan, key held server-side only)
```

GitHub Actions: `ci.yml` (tests, lint, build; its Linux install also proves the pinned requirements work), `deploy-pages.yml`, `deploy-space.yml`. The Docker image pre-fetches the embedding model and tree-sitter grammars at build time so the first visitor does not wait on downloads.

## Free-tier stack and swap-in points

| Layer | Now | Later |
|---|---|---|
| Parsing | tree-sitter | (no change) |
| Graph store | Kùzu, embedded, `api/services/graph_store.py` | Neo4j Aura / self-hosted |
| Users and repos | SQLite, `api/services/user_store.py` | Postgres |
| Embeddings | fastembed, local | hosted API if compute becomes the bottleneck |
| LLM | Groq free tier | paid tier, or another provider behind `call_groq` |
| API hosting | Hugging Face Docker Space (free, ephemeral disk) | persistent volume or a paid host |
| UI hosting | GitHub Pages | (no change) |
| Identity | random per-browser id | a real auth provider behind `get_current_user()` |

## Known limitations

- **Two languages.** Python and JavaScript/JSX only. TypeScript, Java, C#, PHP and COBOL are the realistic next step and each needs a tree-sitter extractor plus its import-resolution rules. Most real legacy code is in those languages, so this is the biggest gap.
- **Subsystems are candidates, not modules.** Clusters come from naming and call structure, not from the build system. They can group files across directories, and a cycle between two clusters means those groupings are entangled, which is useful to know but is not a statement about the repo's own package boundaries.
- **Static analysis has a ceiling.** Reflection, dependency injection, duck typing and cross-service calls are invisible to a parser. The confidence score and `dynamic`/`unresolved` labels exist so that gap is visible rather than hidden. Instance-method resolution covers direct constructor assignments only.
- **LLM output is advisory.** The reference guard stops invented names, not wrong reasoning. The plan says what the structure supports, not what the business needs.
- **Free-tier throughput.** Groq's free tier rate-limits the per-subsystem narrative calls, so an analysis of a mid-size repo takes a few minutes. Groq also retires models with little notice.
- **Ephemeral storage.** On a free Space, user analyses are lost on restart; only the bundled examples persist (they are in the image).
- **Not a security boundary for secrets.** Rate limits are in memory and reset on restart; the client IP is taken from `X-Forwarded-For`. This is fine for a portfolio-scale service and not for adversarial scale.
- **Docker image not built locally.** Development was on Windows without Docker; the first real build happens in the Space. CI's Linux dependency install is the early warning.
