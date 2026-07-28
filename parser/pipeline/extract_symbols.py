"""M2: run the per-language extractors over a repo, dumping one symbol-table
JSON per file. Input for parser/pipeline/link_calls.py (M3).

Usage:
    .venv/Scripts/python parser/pipeline/extract_symbols.py <repo_dir> <output_dir>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from parser.resolvers.python.extract import extract as extract_python
from parser.resolvers.javascript.extract import extract as extract_javascript

EXTENSION_TO_EXTRACTOR = {
    ".py": extract_python,
    ".js": extract_javascript,
    ".jsx": extract_javascript,
}


def main(repo_dir: str, output_dir: str) -> None:
    repo_path = Path(repo_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    extracted = 0
    for file_path in sorted(repo_path.rglob("*")):
        if not file_path.is_file():
            continue
        extractor = EXTENSION_TO_EXTRACTOR.get(file_path.suffix)
        if extractor is None:
            continue
        rel = file_path.relative_to(repo_path)
        result = extractor(file_path, rel)
        out_file = out_path / (str(rel).replace("\\", "__").replace("/", "__") + ".symbols.json")
        out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        extracted += 1

    print(f"Extracted symbol tables for {extracted} files into {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: extract_symbols.py <repo_dir> <output_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
