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

### 1. Parse — `parser/resolvers/{python,javascript,java,csharp,php}/extract.py`
tree-sitter parses each file; the extractor reduces the AST to a per-file symbol table: functions/classes/methods with line ranges, imports, call sites, local `x = Foo()` constructions, dict literals (for dispatch tables) and HTTP routes/`fetch` calls. For Java, C# and PHP (`common.py` holds what they share) it also records the namespace, the declared type of every field, property, parameter and local (`PaymentGateway gateway`), `var x = recv.method()` bindings and each method's declared return type: static typing is what lets those languages be resolved without guessing. Overloads each get their own node (`Class.method`, `Class.method~2`). `repo_walk.py` decides which files are in scope: it skips vendor and build directories, **test code** (tests describe how a system is checked, not how it is built; on `psf/requests` they were 60% of all symbols), minified or oversized files (400 KB for JS, 2 MB for Java/C#/PHP, whose hand-written classes can be large), generated code (`*.Designer.cs`, `*.g.cs`), build output (`target/`, `obj/`, `bin/`, `vendor/`), .NET test projects (`Foo.Tests/`), JUnit/PHPUnit/xUnit-named files, symlinks, and anything listed in the repo's `.rearchignore`.

### 2. Link — `link_calls.py` and `link_nominal.py`
Python and JavaScript are linked by module path in `link_calls.py`. Java, C# and PHP are linked by *class name* in `link_nominal.py`: a name is found through the file's own classes, its imports (including `using Namespace;` and Java wildcard imports), and the enclosing namespaces, nearest first. `gateway.charge()` resolves because `gateway` was declared as a `PaymentGateway`; `Strings.CS.once()` resolves by following one declared field type at a time; `var basket = await repo.Get()` resolves through `Get`'s declared return type (looking through `Task<T>`), but only when exactly one method has that name, because overloads can return different types and picking one would be a guess. Calls on an interface-typed receiver resolve to the *interface's* method: which implementation runs is a dependency-injection fact the parser cannot see. A call on a receiver the project never declares stays `unresolved`; if its declared type belongs to a library it is tagged `external` (`stdlib`, `third-party`, `builtin`, or `library` when an imported namespace could be any of several).

For all languages, the linker resolves:
- imports and direct calls, across files, with Python package-root inference (tries every ancestor of the importing file, nearest first);
- `self.m()` / `this.m()` and `x = Foo(); x.m()` (direct constructor assignments only), following inheritance;
- dict-based dispatch as a `dynamic` fan-out to every value in the table, never collapsed to a guessed target;
- frontend `fetch("/api/x")` matched to backend route strings as `inferred-http`;
- **external dependencies**: an unresolved call whose root name is bound to a non-project import is tagged `stdlib`, `third-party` (with the package name) or `builtin`. This is what lets the migration plan say which subsystem is tied to which framework.

Calls on values the analysis cannot type (`items.append`, `node.child_by_field_name`) stay plain `unresolved`.

### 3. Cluster — `cluster_nodes.py` (embeddings) or `cluster_graph.py` (dependency structure)
Two interchangeable methods, chosen by `REARCH_CLUSTERING`. Both end the same way (renumber by size, keep a class with its methods), so everything after this stage is identical.

**`graph`** (the hosted image's choice) needs no model: Louvain modularity optimisation over the weighted dependency graph, with the same edge weights the migration engine uses, plus a hub vertex per file and per directory so that code which sits together stays together and symbols with no resolved edges still land with their file. Resolution is raised until at least the target count of communities exists, then the smallest are folded into their most-connected neighbour. Pure Python and deterministic. On eShopOnWeb it peaks at 88 MB and 6 s against 1,163 MB and ~25 s for the embedding method, with higher mean cohesion (0.61 vs 0.45, partly by construction: Louvain optimises exactly that kind of measure) and subsystems that follow the project's own directories (`ApplicationCore/Entities`, `BlazorAdmin/Services`).

**`embeddings`** (the local default) groups by meaning: each function/method/class is embedded from structured facts (qualified name, parameters, resolved callers and callees, never raw source) with `fastembed` (`bge-small-en-v1.5`, local ONNX). Then:
1. **HDBSCAN** finds tight cores (no `k` to choose, genuine outliers stay unassigned).
2. **Merge to a target count**, `max(5, round(0.7·√n))` subsystems, folding the smallest cluster into its nearest neighbour. HDBSCAN alone reported 59 "subsystems" for a 37-file library; a plan with dozens of subsystems is noise.
3. **Adopt unassigned nodes** into the subsystem they mostly call and are called by (strictly more than half of their resolved neighbours, so a node that straddles two subsystems stays unassigned).
4. **Keep structure whole**: a class and its methods, a function and its nested functions, always share one subsystem. Embeddings otherwise split them, which fakes coupling.
5. Renumber so `#0` is the largest.

### 4. Narrate — `generate_narratives.py`
This stage is the only one that needs an LLM, and it runs *after* the analysis is usable. `ingest_repo.main(narrate=False)` produces the graph, subsystems and every migration metric with no LLM call (reusing cached narratives for unchanged subsystems), and the API marks the repo `ready` at that point, typically within 4 to 7 seconds. `narrate_repo()` then writes the prose in the background inside `REARCH_NARRATION_BUDGET_SECONDS`: the written plan first (the most useful piece, one call), then subsystem notes largest first with whatever time is left. The page shows "being written" and picks the prose up when it lands. A narration holds no analysis slot, so the next visitor is never queued behind it, and it checks a run id before every write so an older run can never overwrite a newer analysis's numbering. Outcomes: `done`, `partial` (ran out of time part-way), `skipped` (busy, out of time or an error), `quota` (the day's allowance is spent), `unavailable` (no key). This replaced narrating inline, which made a 4-second analysis take 28 to 360 seconds depending on the key's remaining allowance.

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

## Running the API in public

`REARCH_PUBLIC_MODE=1` (set in the Dockerfile) turns on `api/security.py`; local development stays permissive.

- **Repo URLs**: only `https://github.com/<owner>/<repo>`. Always, even locally, anything git could read as an option (`--upload-pack=...`) is refused, and the clone uses `--`, `core.symlinks=false`, `core.longpaths=true` (deep Java/C# package trees exceed Windows' 260-character limit otherwise), a timeout, no credential prompts, no stdin, `--depth 1`. A failed clone's half-written directory is removed so a retry starts clean.
- **Identity**: the `X-User` header is a bearer secret. In public mode it must be a long random id (the browser generates one); short guessable names are refused, so nobody can read another visitor's analyses by guessing a username. A reserved read-only `demo` account holds the bundled examples when seeding is on.
- **Isolation**: all reads go through one `require_repo` check; demo repos are readable by anyone and writable by no one. Commit ids in diff URLs are validated so they cannot escape the snapshots folder. CORS allows only `REARCH_CORS_ORIGINS`.
- **Load**: analyses run as background jobs (the request returns `pending` and the UI polls, because they outlive a hosting proxy's request timeout); one at a time; 3 per visitor per hour and 10 per IP per day; at most `REARCH_MAX_CODE_FILES` analyzable files; unexpected errors are logged server-side and replaced with a generic message so server paths never reach users.
- **Memory** (what makes a 512 MB host viable). Measured on real repositories, each fix found by profiling rather than guessing:
  - clustering without the embedding model (above): 1,163 MB -> 88 MB;
  - the graph database loader sent one statement per node and edge; batching with `UNWIND` took eShopOnWeb's load from ~14 s and ~200 MB to ~0.5 s and ~30 MB (a bulk load otherwise leaves Kùzu holding ~200 MB until it is closed, so a store is closed after loading and reopens itself on the next read, at ~10 MB);
  - a graph database was kept open per analyzed repo forever, each with its own buffer pool; `REARCH_MAX_OPEN_GRAPHS` bounds that and `REARCH_KUZU_BUFFER_MB` sizes the pool from the container, not the host.

  Result: three analyses in one server process (Java, C#, PHP) peak at 152 MB; the densest repo tried (commons-lang, 12,500 edges) at 211 MB. Memory grows ~12 MB per 1,000 edges, which is where the 500-file default for the hosted image comes from.
- **Examples**: `demo/<repo>/` holds pre-computed results (`scripts/build_demo.py`). With seeding on, a fresh server loads them in seconds with no clone and no LLM call. The hosted site turns seeding off (`REARCH_SEED_DEMO=0`) because its UI serves the examples itself and seeding was most of the server's startup time.

## Deployment topology

```
Browser ──> GitHub Pages: React build + the examples as static JSON        (always up)
        └─> Render free web service: API container (FastAPI + Kùzu + SQLite) ──> git clone (public repos), Groq
```

The UI is built with `VITE_STATIC_DEMO=1` and `VITE_API_BASE=<render url>` (the `REARCH_API_URL` repository variable). The switch lives in `web/src/api.js`, so components do not know which mode they are in: requests for an example id are answered from `/demo/<id>/*.json`; everything else goes to the API with the visitor's private id. Without `VITE_API_BASE` the same build is a read-only demo.

A free Render service sleeps after 15 minutes without traffic and needs about a minute to wake, and its disk is temporary. The UI therefore starts polling `/api/health` on load and shows "waking the analysis server" with the Analyze button disabled until it answers (examples work meanwhile), gives up gracefully after 150 s with a pointer to running locally, and labels visitors' analyses as temporary. Render's free plan is 0.1 CPU and 512 MB RAM and allows 750 free instance hours a month (both from Render's documentation).

GitHub Actions: `ci.yml` runs the tests twice (full install, and a lean install of only `requirements-hosted.txt` where `numpy` is not even importable, proving the hosted path does not need it), frontend lint and build, and builds the Docker image and analyzes real Java and C# repositories through it under `--memory=512m --cpus=0.1`; `deploy-pages.yml` publishes the UI.

## Free-tier stack and swap-in points

| Layer | Now | Later |
|---|---|---|
| Parsing | tree-sitter | (no change) |
| Graph store | Kùzu, embedded, `api/services/graph_store.py` | Neo4j Aura / self-hosted |
| Users and repos | SQLite, `api/services/user_store.py` | Postgres |
| Clustering | graph communities (hosted) or fastembed + HDBSCAN (local), `REARCH_CLUSTERING` | hosted embedding API if compute becomes the bottleneck |
| LLM | Groq free tier | paid tier, or another provider behind `call_groq` |
| API hosting | Render free web service (Docker); or run locally | any container host |
| UI hosting | GitHub Pages | (no change) |
| Identity | random per-browser id | a real auth provider behind `get_current_user()` |

## Known limitations

- **Five languages.** Python, JavaScript/JSX, Java, C# and PHP. TypeScript, Kotlin, Go and COBOL are the realistic next ones; each needs a tree-sitter extractor and its import-resolution rules. Beyond that: a Java/C#/PHP receiver is resolved only from a declared type, a `new`, or the return type of a uniquely named method, so `foreach ($routes as $route) { $route->getName() }` and chained fluent calls stay unresolved, and a generic return type (`Task<T>` from `IRepository<Basket>`) is not substituted. Calls through an interface resolve to the interface, not the implementation. Razor/Blazor markup, XML/annotation-driven wiring and PHP framework magic (facades, container resolution) are invisible to the parser.
- **Subsystems are candidates, not modules.** Clusters come from naming and call structure, not from the build system. They can group files across directories, and a cycle between two clusters means those groupings are entangled, which is useful to know but is not a statement about the repo's own package boundaries.
- **Static analysis has a ceiling.** Reflection, dependency injection, duck typing and cross-service calls are invisible to a parser. The confidence score and `dynamic`/`unresolved` labels exist so that gap is visible rather than hidden. Instance-method resolution covers direct constructor assignments only.
- **LLM output is advisory.** The reference guard stops invented names, not wrong reasoning. The plan says what the structure supports, not what the business needs.
- **The free LLM allowance is small and shared.** For `openai/gpt-oss-120b` Groq's free tier allows 8,000 tokens a minute *and* 200,000 a day (both from Groq's own rate-limit message), shared by every visitor and by development. A narrated analysis costs on the order of 10,000 tokens (my estimate: a prompt and up to 2,500 output tokens for the plan, then up to 900 per subsystem note), so roughly twenty a day. When the allowance is spent the written parts are skipped and the page says so; everything measured is unaffected. The client treats the per-minute limit as something to wait out and the daily limit as a hard stop (waiting only burns time), and both are bounded by the narration budget. Groq also retires models with little notice.
- **The free host is small and sleeps.** 512 MB / 0.1 CPU caps analyses at 500 files; speed is the least certain figure: CI analyzes spring-petclinic and eShopOnWeb through the image in about 30 seconds in total under `--cpus=0.1`, but that is a fast GitHub runner throttled by Docker, and Render's shared 0.1 CPU may be slower, so treat large repositories as taking minutes; the first request after idle waits about a minute; and results do not survive a restart. Graph clustering is what makes this possible, and it groups by coupling rather than by meaning, so its subsystems can differ from the embedding method's on the same repository.
- **Not a security boundary for secrets.** Rate limits are in memory and reset on restart; the client IP is taken from `X-Forwarded-For`. This is fine for a portfolio-scale service and not for adversarial scale.
- **The Docker image is verified only in CI.** Development was on Windows without Docker, so the image is built and exercised by the `docker` CI job (under Render's memory and CPU limits; it passes), not by the author locally. The memory figures above come from the same configuration run natively in a clean venv of only `requirements-hosted.txt`.
