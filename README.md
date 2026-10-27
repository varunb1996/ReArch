# ReArch — Reverse Architect

**Decode the Past. Design the Future.**

**Live: https://varunb1996.github.io/ReArch/** — five bundled examples (Java, C#, PHP, Python, JavaScript) open instantly; paste a public GitHub URL to analyze your own.

ReArch reverse-engineers an undocumented codebase into an interactive architecture blueprint and a **migration plan**: which parts can be extracted first, which are tangled in dependency cycles that must be cut before anything moves, what interface each part already exposes, and how far the analysis itself can be trusted for each one.

It does this by building a verified structural graph first (tree-sitter, cross-file call resolution), clustering it into candidate subsystems, measuring coupling between them, and only then asking an LLM to write the plan in words. The LLM never discovers structure and never sees raw source dumps. Every name in its output is checked against the measured facts, and anything unverified is flagged.

## What you get

- **Blueprint**: an interactive dependency graph, colored by subsystem, with edges labelled by how sure the analysis is (resolved, dynamic dispatch, inferred cross-language HTTP, unresolved).
- **Migration plan**: subsystems ordered into extraction waves (leaves first), dependency cycles with the cheapest links to cut, the entry points each subsystem already exposes (its seams), third-party/framework coupling, and a readiness label with the reasons behind it. Downloadable as Markdown.
- **Intent narratives**: a "why does this subsystem exist" paragraph per subsystem, shown next to the facts that produced it.
- **Version-aware diffing** between analyzed commits, and a **GitHub webhook** that re-analyzes on every push.

Languages: **Python, JavaScript/JSX, Java, C# and PHP**, including calls across Python and JavaScript (inferred from route strings). Each has a tree-sitter extractor; Java, C# and PHP are linked by class name through namespaces, imports and declared types (see [ARCHITECTURE.md](ARCHITECTURE.md#2-link--link_callspy-and-link_nominalpy)). Adding a language means adding an extractor.

## Run it locally

Requires **Python 3.13** (not 3.14 yet: `kuzu` and `tree-sitter-language-pack` lack 3.14 wheels on Windows), Node 20+, and git.

```bash
py -3.13 -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp .env.example .env        # optional: add GROQ_API_KEY for written narratives and plans
.venv/Scripts/uvicorn api.main:app --port 8000
```

```bash
cd web
npm install
npm run dev                 # http://localhost:5173
```

Enter any username, paste a git URL (a local path also works in local mode), and press Analyze. Without `GROQ_API_KEY` you still get the full blueprint and all migration metrics; only the written prose is skipped.

### Tests

```bash
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```

The suite covers the linker against a hand-verified fixture, the migration engine on synthetic graphs with known answers, the access-control and URL-safety rules a public deployment depends on, and the background-analysis flow.

## The hosted site (free)

Two free pieces, each doing what it is good at:

```
Browser ──> GitHub Pages: the UI + the bundled examples as static JSON      (always up, instant)
        └─> Render free web service: the API that analyzes visitors' repos   (sleeps when idle)
```

- **The examples never wait for a server.** They are pre-analyzed (`demo/`) and served as static files, so they open instantly even while the API is asleep.
- **Your own repository** is analyzed by the API. A visitor pastes a public `https://github.com/<owner>/<repo>` URL; the analysis runs as a background job and the page polls until it is ready. Each visitor gets a private random id, results are private to that browser, and the free server's disk is temporary (results disappear when it restarts).
- **Limits** (see [ARCHITECTURE.md](ARCHITECTURE.md#running-the-api-in-public)): public GitHub URLs only, 500 analyzable files, 3 analyses per visitor per hour, 10 per IP per day, one analysis at a time.
- **No Groq key is needed to use it.** Without `GROQ_API_KEY` on the server you still get the blueprint and every migration metric; the key only adds the written narratives and plan.

### Deploy it yourself (about 10 minutes)

1. **GitHub Pages**: repo **Settings → Pages → Source: GitHub Actions**. Every push to `main` publishes the UI and examples to `https://<owner>.github.io/ReArch/`.
2. **Render**: dashboard → **New → Blueprint** → pick this repository. `render.yaml` defines a free Docker web service named `rearch-api`. Optionally set `GROQ_API_KEY` in the Render dashboard (it is never stored in git). Edit `REARCH_CORS_ORIGINS` in `render.yaml` if your UI is not on `github.io/<owner>`.
3. **Connect them**: in the GitHub repo, **Settings → Secrets and variables → Actions → Variables → New repository variable** `REARCH_API_URL` = your Render URL (for example `https://rearch-api.onrender.com`), then re-run **Deploy demo to GitHub Pages**. Without the variable the site is a read-only demo.
4. **Check it** with the smoke test, which analyzes real repos through the API like a visitor does:

```bash
python scripts/smoke_hosted.py https://rearch-api.onrender.com https://github.com/spring-projects/spring-petclinic
```

**Why the hosted image is built differently.** Render's free service has 512 MB RAM and 0.1 CPU. The default pipeline clusters with a sentence-embedding model and scikit-learn, which peaked at 1.2 GB on a 206-file repo, so the hosted image sets `REARCH_CLUSTERING=graph` and installs only `requirements-hosted.txt` (no numpy, scikit-learn or model). With that, the same repo peaks at about 150 MB. CI builds the image and analyzes real repositories inside it under `--memory=512m --cpus=0.1`, so an out-of-memory regression fails the build.

To refresh or add the bundled examples: `.venv/Scripts/python scripts/build_demo.py https://github.com/psf/requests`, then commit `demo/`. The Pages build converts `demo/` into the static files the UI reads (`scripts/export_static_demo.py`).

### Configuration

| Variable | Where | Meaning |
|---|---|---|
| `GROQ_API_KEY` | API / scripts | Enables written narratives and plans. Optional. |
| `GROQ_MODEL` | API / scripts | Defaults to `openai/gpt-oss-120b`. Groq retires models; `GET https://api.groq.com/openai/v1/models` lists what your key can use. |
| `WEBHOOK_SECRET` | API | Enables `POST /api/webhooks/github`. |
| `REARCH_PUBLIC_MODE` | API | `1` turns on the public-deployment guardrails. |
| `REARCH_CLUSTERING` | API / scripts | `embeddings` (default: sentence model + HDBSCAN) or `graph` (dependency-structure communities: no model, ~10x less RAM). The Docker image uses `graph`. |
| `REARCH_SEED_DEMO` | API | `0` skips loading the bundled examples at startup (the hybrid site serves them statically). Default: on in public mode. |
| `REARCH_KUZU_BUFFER_MB` | API | Caps each graph database's buffer pool (default: Kùzu's 80% of host RAM). |
| `REARCH_MAX_OPEN_GRAPHS` | API | Keeps at most this many graph databases open, closing the least recently used (default: unlimited). |
| `REARCH_CORS_ORIGINS` | API | Comma-separated allowed browser origins. |
| `REARCH_DATA_DIR` | API | Where analyses and the user database are stored. |
| `REARCH_MAX_CODE_FILES` | API | Largest repository accepted (default 3000 analyzable files). |
| `VITE_API_BASE` | UI build | Backend URL. Defaults to `http://localhost:8000`. With `VITE_STATIC_DEMO=1` it enables live analysis next to the static examples. |
| `VITE_STATIC_DEMO` | UI build | `1` serves the bundled examples as static files (what Pages does). Alone it is a read-only demo. |

## Project layout

```
parser/
  schema/graph-node.schema.json   the node/edge contract every stage conforms to
  resolvers/{python,javascript,java,csharp,php}/   per-language symbol and call extraction (tree-sitter)
  pipeline/                       extract -> link -> cluster -> narrate -> migration plan -> snapshot
                                  (link_nominal.py: Java/C#/PHP; cluster_graph.py: embedding-free clustering)
api/
  main.py                         FastAPI app and routes
  security.py                     public-deployment guardrails
  services/                       Kùzu graph store, SQLite user/repo store
web/                              React + React Flow UI
demo/                             pre-analyzed examples (the hosted demo's data)
scripts/build_demo.py             regenerate those examples
scripts/smoke_hosted.py           drive a running API like a visitor (CI and post-deploy check)
tests/                            pytest suite
Dockerfile, render.yaml           the hosted API image and its Render blueprint
requirements-hosted.txt           lean dependency set for that image
.github/workflows/               CI and the GitHub Pages deploy
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for how each stage works, why the design is the way it is, and what it cannot do.
