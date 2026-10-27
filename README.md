# ReArch — Reverse Architect

**Decode the Past. Design the Future.**

ReArch reverse-engineers an undocumented codebase into an interactive architecture blueprint and a **migration plan**: which parts can be extracted first, which are tangled in dependency cycles that must be cut before anything moves, what interface each part already exposes, and how far the analysis itself can be trusted for each one.

It does this by building a verified structural graph first (tree-sitter, cross-file call resolution), clustering it into candidate subsystems, measuring coupling between them, and only then asking an LLM to write the plan in words. The LLM never discovers structure and never sees raw source dumps. Every name in its output is checked against the measured facts, and anything unverified is flagged.

## What you get

- **Blueprint**: an interactive dependency graph, colored by subsystem, with edges labelled by how sure the analysis is (resolved, dynamic dispatch, inferred cross-language HTTP, unresolved).
- **Migration plan**: subsystems ordered into extraction waves (leaves first), dependency cycles with the cheapest links to cut, the entry points each subsystem already exposes (its seams), third-party/framework coupling, and a readiness label with the reasons behind it. Downloadable as Markdown.
- **Intent narratives**: a "why does this subsystem exist" paragraph per subsystem, shown next to the facts that produced it.
- **Version-aware diffing** between analyzed commits, and a **GitHub webhook** that re-analyzes on every push.

Languages today: **Python and JavaScript/JSX**, including calls across the two (inferred from route strings). Adding a language means adding a tree-sitter extractor; see [ARCHITECTURE.md](ARCHITECTURE.md).

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

## The hosted demo (GitHub Pages, free)

The public page is a **static site**: no server, no cost, no account beyond GitHub, nothing to abuse. It serves pre-analyzed real repositories (`demo/`), so the subsystem map, the detailed graph and the full migration plan are all explorable in a browser.

One-time setup: repo **Settings → Pages → Source: GitHub Actions**. After that, every push to `main` runs **Deploy demo to GitHub Pages** and publishes to `https://<owner>.github.io/ReArch/`.

**Analyzing your own repository happens locally** (see above, two commands). That is deliberate: analysis needs git, native parsers, an embedded graph database and an LLM key, which a free static host cannot run, and the free server tiers are either paid (Hugging Face Docker Spaces require a paid plan) or too small for the embedding model.

To refresh or add examples: `.venv/Scripts/python scripts/build_demo.py https://github.com/psf/requests`, then commit `demo/`. The Pages build converts `demo/` into the static files the UI reads (`scripts/export_static_demo.py`).

## Self-hosting the API (optional)

If you do want live analysis online, the `Dockerfile` builds the API in *public mode* (`REARCH_PUBLIC_MODE=1`): only public `https://github.com/<owner>/<repo>` URLs, private per-browser visitor ids, a read-only example account, rate limits (3 per visitor per hour, 10 per IP per day), one analysis at a time. Build the UI with `VITE_API_BASE` pointing at it and set `REARCH_CORS_ORIGINS` on the API. The image has not been built yet, so treat the first build as a test. Details in [ARCHITECTURE.md](ARCHITECTURE.md#running-the-api-in-public).

### Configuration

| Variable | Where | Meaning |
|---|---|---|
| `GROQ_API_KEY` | API / scripts | Enables written narratives and plans. Optional. |
| `GROQ_MODEL` | API / scripts | Defaults to `openai/gpt-oss-120b`. Groq retires models; `GET https://api.groq.com/openai/v1/models` lists what your key can use. |
| `WEBHOOK_SECRET` | API | Enables `POST /api/webhooks/github`. |
| `REARCH_PUBLIC_MODE` | API | `1` turns on the public-deployment guardrails. |
| `REARCH_CORS_ORIGINS` | API | Comma-separated allowed browser origins. |
| `REARCH_DATA_DIR` | API | Where analyses and the user database are stored. |
| `REARCH_MAX_CODE_FILES` | API | Largest repository accepted (default 3000 analyzable files). |
| `VITE_API_BASE` | UI build | Backend URL. Defaults to `http://localhost:8000`. |
| `VITE_STATIC_DEMO` | UI build | `1` builds the serverless demo (what Pages serves). |

## Project layout

```
parser/
  schema/graph-node.schema.json   the node/edge contract every stage conforms to
  resolvers/{python,javascript}/  per-language symbol and call extraction (tree-sitter)
  pipeline/                       extract -> link -> cluster -> narrate -> migration plan -> snapshot
api/
  main.py                         FastAPI app and routes
  security.py                     public-deployment guardrails
  services/                       Kùzu graph store, SQLite user/repo store
web/                              React + React Flow UI
demo/                             pre-analyzed examples (the hosted demo's data)
scripts/build_demo.py             regenerate those examples
tests/                            pytest suite
Dockerfile                        optional self-hosting image for the API
.github/workflows/               CI and the GitHub Pages deploy
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for how each stage works, why the design is the way it is, and what it cannot do.
