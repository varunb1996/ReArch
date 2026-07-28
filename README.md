# ReArch — Reverse Architect

**Decode the Past. Design the Future.**

ReArch reverse-engineers undocumented legacy codebases into interactive, explorable system blueprints, so teams can plan modernization without spelunking through years of un-commented code first. Unlike static-visualization tools, ReArch builds a verified structural graph first, then grounds an LLM in that graph (never in raw source dumps) to explain *why* a subsystem likely exists — with every claim traceable back to the facts that produced it.

Built entirely on free tools as a solo side project, with a clear path to paid infrastructure once there's traction. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

## Status

All 14 milestones from the original build plan are implemented and verified against real code (both a hand-crafted test fixture and this repository's own source, ingested by the tool itself):

| Phase | Milestones | Status |
|---|---|---|
| 0 — Structural core | Tree-sitter parsing, symbol extraction, cross-file/cross-language call resolution | Done |
| 1 — Storage + viewing | Kùzu graph store, FastAPI + React Flow blueprint viewer | Done |
| 2 — Real ingestion | Clone-and-analyze any git repo, multi-user isolation | Done |
| 3 — Semantic layer | Embedding + clustering nodes into candidate subsystems | Done |
| 4 — LLM narratives | Grounded "why this exists" generation via Groq, with caching | Done |
| 5 — Productization | Search/focus UI, version-aware diffing, GitHub webhook auto-sync | Done |

This is a demo-scale MVP, not a production service — see [ARCHITECTURE.md § Known Limitations](ARCHITECTURE.md#known-limitations) for what that means concretely.

## Quick start

### Prerequisites

- **Python 3.13** specifically (not 3.14 — `kuzu` and `tree-sitter-language-pack` don't yet ship Windows wheels for 3.14). If you have multiple Python versions via the `py` launcher: `py -3.13 -m venv .venv`.
- **Node.js** (any recent LTS) for the frontend.
- **git** on PATH.

### Backend setup

```bash
py -3.13 -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in:
- `GROQ_API_KEY` — free at [console.groq.com](https://console.groq.com), no card required. Without it, narrative generation is silently skipped (everything else still works).
- `WEBHOOK_SECRET` — any random string, only needed if you're wiring up `POST /api/webhooks/github` for push-triggered re-analysis.

Run the API:

```bash
.venv/Scripts/uvicorn api.main:app --reload --port 8000
```

### Frontend setup

```bash
cd web
npm install
npm run dev
```

Open `http://localhost:5173`, enter any username (there's no real auth yet — see [ARCHITECTURE.md § Multi-user model](ARCHITECTURE.md#multi-user-model)), and paste a git URL to analyze.

### Running the pipeline standalone

Each pipeline stage is also a runnable script, useful for debugging one stage in isolation:

```bash
.venv/Scripts/python parser/pipeline/ingest_repo.py <git_url> <work_dir>
```

runs the whole pipeline (clone → extract → resolve → cluster → narrate → snapshot) and prints progress for each stage. See [ARCHITECTURE.md § Pipeline](ARCHITECTURE.md#pipeline) for what each stage does and its individual script.

## Project structure

```
parser/
  schema/graph-node.schema.json   canonical node/edge schema — read this first
  resolvers/{python,javascript}/  per-language symbol/call extraction
  pipeline/                       the 6-stage pipeline, each stage a standalone script
api/
  main.py                        FastAPI app: repos, graph, narratives, diff, webhook routes
  services/graph_store.py        Kùzu graph storage (Neo4j stand-in)
  services/user_store.py         SQLite user/repo store (Supabase stand-in)
web/
  src/App.jsx                    React Flow blueprint viewer, repo manager, diff UI
fixtures/sample_repo/            hand-crafted multi-language test repo with known ground truth
```

## Testing changes

There's no formal test suite yet — verification has been done by:
1. Running the pipeline against `fixtures/sample_repo/GROUND_TRUTH.md`'s hand-verified expected edges.
2. Running it against this repo's own real source as an uncurated end-to-end check.
3. Manual verification of the running UI via browser automation during development.

If you change the resolver/pipeline logic, re-run both checks before trusting the output.
