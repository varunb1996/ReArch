"""Shared file-walking logic for the parsing pipeline.

Split out because M7 (ingesting arbitrary real repos) exposed a bug the
hand-crafted fixture never could: a naive rglob over a real repo walks
node_modules/.venv/.git too, which is both slow and pollutes the graph with
third-party library internals instead of the target system's own structure.
"""
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build",
    "__pycache__", ".next", ".cache", "coverage", ".pytest_cache", ".mypy_cache",
}


def iter_code_files(repo_path: Path, extensions: set[str]):
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(repo_path).parts[:-1]
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if path.suffix in extensions:
            yield path
