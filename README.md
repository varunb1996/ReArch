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

## Deploy it

The UI is static (GitHub Pages); the API needs a real process (git, tree-sitter, an embedded graph database), so it runs as a Docker image on a free **Hugging Face Space**. Both deploy automatically from GitHub Actions on every push to `main`. One-time setup:

1. **API**: create a Space at huggingface.co/new-space (SDK: **Docker**, blank). In the Space's *Settings → Variables and secrets* add the secret `GROQ_API_KEY`.
2. In this GitHub repo, *Settings → Secrets and variables → Actions*: add secret `HF_TOKEN` (a Hugging Face token with **write** access) and variable `HF_SPACE` (`owner/space-name`). Push to `main`, or run **Deploy API to Hugging Face Space** manually.
3. **UI**: add the repository variable `API_BASE` with the Space's URL (`https://<owner>-<space-name>.hf.space`), and set *Settings → Pages → Source* to **GitHub Actions**. The **Deploy UI to GitHub Pages** workflow publishes to `https://<owner>.github.io/ReArch/`.

The hosted server runs in *public mode* (`REARCH_PUBLIC_MODE=1`, set by the Dockerfile): only public `https://github.com/<owner>/<repo>` URLs are accepted, each browser gets a private random id instead of a username, example analyses bundled in `demo/` are read-only and load instantly, analyses are rate limited (3/hour per visitor, 10/day per IP) and run one at a time. Details in [ARCHITECTURE.md](ARCHITECTURE.md#running-it-in-public).

To refresh the bundled examples: `.venv/Scripts/python scripts/build_demo.py https://github.com/psf/requests`, then commit `demo/`.

### Configuration

| Variable | Where | Meaning |
|---|---|---|
| `GROQ_API_KEY` | API | Enables written narratives and plans. Optional. |
| `GROQ_MODEL` | API | Defaults to `openai/gpt-oss-120b`. Groq retires models; `GET https://api.groq.com/openai/v1/models` lists what your key can use. |
| `WEBHOOK_SECRET` | API | Enables `POST /api/webhooks/github`. |
| `REARCH_PUBLIC_MODE` | API | `1` turns on the public-deployment guardrails. |
| `REARCH_CORS_ORIGINS` | API | Comma-separated allowed browser origins. |
| `REARCH_DATA_DIR` | API | Where analyses and the user database are stored. |
| `REARCH_MAX_CODE_FILES` | API | Largest repository accepted (default 3000 analyzable files). |
| `VITE_API_BASE` | UI build | Backend URL. Defaults to `http://localhost:8000`. |

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
demo/                             bundled example analyses for the hosted demo
scripts/build_demo.py             regenerate those examples
tests/                            pytest suite
Dockerfile, .github/workflows/    deployment
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for how each stage works, why the design is the way it is, and what it cannot do.
