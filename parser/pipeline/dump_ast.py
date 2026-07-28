"""M1: walk a repo, parse every recognized file with tree-sitter, dump raw ASTs to JSON.

Usage:
    .venv/Scripts/python parser/pipeline/dump_ast.py <repo_dir> <output_dir>
"""
import json
import sys
from pathlib import Path

import tree_sitter_language_pack as tslp

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from parser.pipeline.repo_walk import iter_code_files

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
}

_PARSER_CACHE: dict[str, object] = {}


def get_parser(language: str):
    if language not in _PARSER_CACHE:
        _PARSER_CACHE[language] = tslp.get_parser(language)
    return _PARSER_CACHE[language]


def node_to_dict(node, source: bytes) -> dict:
    result = {
        "type": node.type,
        "start_point": list(node.start_point),
        "end_point": list(node.end_point),
        "start_byte": node.start_byte,
        "end_byte": node.end_byte,
    }
    if not node.children:
        text = source[node.start_byte:node.end_byte]
        try:
            result["text"] = text.decode("utf-8")
        except UnicodeDecodeError:
            result["text"] = repr(text)
    else:
        result["children"] = [node_to_dict(child, source) for child in node.children]
    return result


def parse_file(path: Path) -> dict | None:
    language = EXTENSION_TO_LANGUAGE.get(path.suffix)
    if language is None:
        return None
    source = path.read_bytes()
    parser = get_parser(language)
    tree = parser.parse(source)
    return {
        "path": str(path),
        "language": language,
        "ast": node_to_dict(tree.root_node, source),
    }


def main(repo_dir: str, output_dir: str) -> None:
    repo_path = Path(repo_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    parsed_count = 0
    skipped = []
    for file_path in iter_code_files(repo_path, set(EXTENSION_TO_LANGUAGE)):
        result = parse_file(file_path)
        if result is None:
            skipped.append(str(file_path))
            continue
        rel = file_path.relative_to(repo_path)
        out_file = out_path / (str(rel).replace("\\", "__").replace("/", "__") + ".ast.json")
        out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        parsed_count += 1

    print(f"Parsed {parsed_count} files into {out_path}")
    if skipped:
        print(f"Skipped {len(skipped)} non-code files (e.g. {skipped[:3]})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: dump_ast.py <repo_dir> <output_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
