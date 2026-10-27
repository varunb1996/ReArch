"""Build a bundled example analysis for the hosted demo.

    .venv/Scripts/python scripts/build_demo.py https://github.com/psf/requests [display-name]

Runs the full pipeline (including the Groq-written migration plan if
GROQ_API_KEY is in .env) in a temp folder and copies only the finished results
into demo/<slug>/. The hosted server loads these at startup (api/main.py,
seed_demo_repos), so a fresh visitor sees real analyses instantly and the
server never has to clone or call an LLM just to show the examples.
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from parser.pipeline.ingest_repo import main as ingest  # noqa: E402

KEEP = ("graph.json", "narratives.json", "migration.json")


def main(git_url: str, name: str | None = None) -> None:
    slug = git_url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1].lower()
    with tempfile.TemporaryDirectory() as work:
        result = ingest(git_url, work)
        target = ROOT / "demo" / slug
        target.mkdir(parents=True, exist_ok=True)
        for filename in KEEP:
            source = Path(work) / filename
            if source.is_file():
                shutil.copyfile(source, target / filename)
        meta = {
            "name": name or slug,
            "git_url": git_url,
            "commit": result["commit_sha"],
            "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (target / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote demo/{slug} (commit {meta['commit'][:10]})")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        sys.exit(1)
    main(*sys.argv[1:])
