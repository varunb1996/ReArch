"""M7: clone/pull an arbitrary git repo and run the pipeline end-to-end.

This is the local-only stand-in for the eventual upload/GitHub-connect flow:
same stages (extract_symbols -> link_calls), just triggered by a git URL
instead of a web upload, and running synchronously instead of on a queued
background worker. Swap points for the real ingestion pipeline (R2 storage,
Cloudflare Queues, GitHub OAuth) are exactly the two functions below.

Usage:
    .venv/Scripts/python parser/pipeline/ingest_repo.py <git_url> <work_dir>
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from parser.pipeline.extract_symbols import main as extract_symbols_main
from parser.pipeline.link_calls import main as link_calls_main


def clone_or_update(git_url: str, dest: Path) -> None:
    if dest.exists():
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"], check=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", git_url, str(dest)], check=True)


def main(git_url: str, work_dir: str) -> Path:
    work_path = Path(work_dir)
    repo_dir = work_path / "repo"
    symbols_dir = work_path / "symbols"
    graph_path = work_path / "graph.json"

    print(f"Cloning/updating {git_url} -> {repo_dir}")
    clone_or_update(git_url, repo_dir)

    print("Extracting symbol tables...")
    extract_symbols_main(str(repo_dir), str(symbols_dir))

    print("Resolving calls/imports into graph...")
    link_calls_main(str(symbols_dir), str(graph_path))

    return graph_path


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: ingest_repo.py <git_url> <work_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
