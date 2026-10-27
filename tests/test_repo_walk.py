from parser.pipeline.repo_walk import iter_code_files


def make(tmp_path, files):
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def names(tmp_path):
    return sorted(p.relative_to(tmp_path).as_posix() for p in iter_code_files(tmp_path, {".py", ".js"}))


def test_vendor_dirs_and_other_extensions_are_skipped(tmp_path):
    make(tmp_path, {"app.py": "x", "node_modules/lib/a.js": "x", ".venv/b.py": "x", "notes.md": "x"})
    assert names(tmp_path) == ["app.py"]


def test_rearchignore_prefixes_skip_whole_directories_but_not_lookalikes(tmp_path):
    make(tmp_path, {
        ".rearchignore": "# comment\nfixtures/\n",
        "src/app.py": "x", "fixtures/fake.py": "x", "fixtures_real/keep.py": "x",
    })
    assert names(tmp_path) == ["fixtures_real/keep.py", "src/app.py"]


def test_minified_and_oversized_files_are_skipped(tmp_path):
    make(tmp_path, {"ok.js": "x", "vendor.min.js": "x", "huge.js": "x" * 500_000})
    assert names(tmp_path) == ["ok.js"]


def test_test_code_is_skipped_by_default(tmp_path):
    make(tmp_path, {
        "src/app.py": "x", "tests/test_app.py": "x", "src/test_helpers.py": "x", "src/thing_test.py": "x",
        "conftest.py": "x", "web/app.test.js": "x", "web/app.js": "x", "__tests__/a.js": "x",
    })
    assert names(tmp_path) == ["src/app.py", "web/app.js"]
