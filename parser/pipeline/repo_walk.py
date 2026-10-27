"""Shared file-walking logic for the parsing pipeline.

Split out because M7 (ingesting arbitrary real repos) exposed a bug the
hand-crafted fixture never could: a naive rglob over a real repo walks
node_modules/.venv/.git too, which is both slow and pollutes the graph with
third-party library internals instead of the target system's own structure.

A repo can also opt paths out of analysis with a `.rearchignore` file at its
root: one path prefix per line (`fixtures/`, `docs/examples`), `#` for comments.
Useful for test fixtures, vendored code and generated output that would
otherwise show up as fake subsystems.
"""
import re
from pathlib import Path

# Test code describes how a system is checked, not how it is built. On psf/requests
# 60% of all symbols were tests, which drowned the real subsystems in hundreds of
# tiny "test_x" clusters, so tests are skipped by default.
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "testing", "integrationtests", "unittests"}
TEST_FILE = re.compile(
    r"(test_.*\.py|.*_test\.py|conftest\.py|.*\.(test|spec)\.jsx?"
    r"|.*(Test|Tests|IT|TestCase)\.(java|php)|.*(Test|Tests)\.cs)"  # JUnit/PHPUnit/xUnit naming
)
# .NET test projects are whole directories named Foo.Tests / Foo.UnitTests.
TEST_DIR_SUFFIXES = (".tests", ".test", ".unittests", ".integrationtests")

EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build",
    "__pycache__", ".next", ".cache", "coverage", ".pytest_cache", ".mypy_cache",
    "vendor", "target", ".gradle", ".idea", ".vs", "obj",  # Composer/Maven/Gradle/IDE/MSBuild output
} | TEST_DIRS
# Compiled-output directories whose names are legitimate source folders in other languages
# (`bin/` scripts in a Python repo), so they only count for the languages that compile into them.
EXCLUDED_DIRS_BY_SUFFIX = {".cs": {"bin"}, ".java": {"bin", "out"}}

# Minified bundles and giant generated files are slow to parse and carry no
# architecture; real legacy repos are full of them.
MAX_FILE_BYTES = 400_000
# Hand-written Java/C#/PHP classes can be large (commons-lang's StringUtils.java is over 400 KB) and
# are not minified, so the limit that suits JS bundles would silently drop core classes.
MAX_FILE_BYTES_BY_SUFFIX = {".java": 2_000_000, ".cs": 2_000_000, ".php": 2_000_000}
SKIPPED_SUFFIXES = (".min.js", ".bundle.js", ".Designer.cs", ".g.cs", ".generated.cs", ".g.i.cs")


def load_ignore_prefixes(repo_path: Path) -> list[str]:
    ignore_file = repo_path / ".rearchignore"
    if not ignore_file.is_file():
        return []
    prefixes = []
    for line in ignore_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            prefixes.append(line.replace("\\", "/").strip("/"))
    return prefixes


def is_ignored(rel_posix: str, prefixes: list[str]) -> bool:
    return any(rel_posix == p or rel_posix.startswith(p + "/") for p in prefixes)


def iter_code_files(repo_path: Path, extensions: set[str]):
    prefixes = load_ignore_prefixes(repo_path)
    for path in sorted(repo_path.rglob("*")):
        # Symlinks in an untrusted repo can point anywhere on this machine.
        if path.is_symlink() or not path.is_file() or path.suffix not in extensions:
            continue
        relative = path.relative_to(repo_path)
        directories = relative.parts[:-1]
        excluded = EXCLUDED_DIRS | EXCLUDED_DIRS_BY_SUFFIX.get(path.suffix, set())
        if any(part in excluded or part.lower().endswith(TEST_DIR_SUFFIXES) for part in directories):
            continue
        if is_ignored(relative.as_posix(), prefixes):
            continue
        if TEST_FILE.fullmatch(path.name):
            continue
        if path.name.endswith(SKIPPED_SUFFIXES) or path.stat().st_size > MAX_FILE_BYTES_BY_SUFFIX.get(path.suffix, MAX_FILE_BYTES):
            continue
        yield path
