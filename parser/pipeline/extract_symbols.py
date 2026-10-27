"""M2: run the per-language extractors over a repo, dumping one symbol-table
JSON per file. Input for parser/pipeline/link_calls.py (M3).

Usage:
    .venv/Scripts/python parser/pipeline/extract_symbols.py <repo_dir> <output_dir>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from parser.pipeline.repo_walk import iter_code_files
from parser.resolvers.python.extract import extract as extract_python
from parser.resolvers.javascript.extract import extract as extract_javascript
from parser.resolvers.java.extract import extract as extract_java
from parser.resolvers.csharp.extract import extract as extract_csharp
from parser.resolvers.php.extract import extract as extract_php

EXTENSION_TO_EXTRACTOR = {
    ".py": extract_python,
    ".js": extract_javascript,
    ".jsx": extract_javascript,
    ".java": extract_java,
    ".cs": extract_csharp,
    ".php": extract_php,
}


def main(repo_dir: str, output_dir: str) -> None:
    repo_path = Path(repo_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    extracted = 0
    errors = []
    for file_path in iter_code_files(repo_path, set(EXTENSION_TO_EXTRACTOR)):
        # The repo-relative path is stored inside the file. Using it as the file name too would hit
        # the OS path limit (260 characters on Windows) on deep Java/C# package trees, so the name is
        # just a sortable index plus a readable tail.
        extractor = EXTENSION_TO_EXTRACTOR[file_path.suffix]
        rel = file_path.relative_to(repo_path)
        try:
            result = extractor(file_path, rel)
        except Exception as exc:
            errors.append((str(rel), str(exc)))
            continue
        out_file = out_path / f"{extracted:06d}-{file_path.name[:60]}.symbols.json"
        out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        extracted += 1

    print(f"Extracted symbol tables for {extracted} files into {out_path}")
    if errors:
        print(f"Failed to parse {len(errors)} files:")
        for rel, msg in errors[:20]:
            print(f"  {rel}: {msg}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: extract_symbols.py <repo_dir> <output_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
