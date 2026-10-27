"""M7: clone/pull an arbitrary git repo and run the pipeline end-to-end.

This is the local-only stand-in for the eventual upload/GitHub-connect flow:
same stages (extract_symbols -> link_calls), just triggered by a git URL
instead of a web upload, and running synchronously instead of on a queued
background worker. Swap points for the real ingestion pipeline (R2 storage,
Cloudflare Queues, GitHub OAuth) are exactly the two functions below.

Usage:
    .venv/Scripts/python parser/pipeline/ingest_repo.py <git_url> <work_dir>
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from parser.pipeline.extract_symbols import EXTENSION_TO_EXTRACTOR, main as extract_symbols_main
from parser.pipeline.repo_walk import iter_code_files
from parser.pipeline.link_calls import main as link_calls_main
from parser.pipeline.cluster_nodes import main as cluster_nodes_main
from parser.pipeline.generate_narratives import main as generate_narratives_main
from parser.pipeline.migration_plan import main as migration_plan_main


GIT_TIMEOUT_SECONDS = 180
MAX_CODE_FILES = int(os.environ.get("REARCH_MAX_CODE_FILES", "3000"))
# Never let git stop and wait for a password on a private/typo'd repo.
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def clone_or_update(git_url: str, dest: Path) -> None:
    """Shallow-clone (or fast-forward) an untrusted repository.

    `--` stops a URL like `--upload-pack=...` being read as a git option,
    core.symlinks=false stops symlinks in the repo pointing at server files,
    core.longpaths lets Windows check out deep Java/C# package paths (past 260
    characters git otherwise fails with "Filename too long"), and the error is
    rewritten so server paths never reach the user.
    """
    if dest.exists():
        command = ["git", "-C", str(dest), "pull", "--ff-only"]
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "-c", "core.symlinks=false", "-c", "core.longpaths=true", "clone", "--depth", "1", "--single-branch", "--no-tags", "--", git_url, str(dest)]
    fresh_clone = not dest.exists()
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=GIT_TIMEOUT_SECONDS, env=GIT_ENV, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        discard_partial_clone(dest, fresh_clone)
        raise RuntimeError(f"Cloning took longer than {GIT_TIMEOUT_SECONDS}s; the repository is too large for this server.") from None
    except subprocess.CalledProcessError as exc:
        # The user only sees the curated message below; the real reason goes to the server log.
        print(f"git failed ({exc.returncode}): {exc.stderr.decode(errors='replace').strip()[-400:]}")
        discard_partial_clone(dest, fresh_clone)
        raise RuntimeError("Could not clone the repository. Check the URL and that the repository is public.") from None


def discard_partial_clone(dest: Path, fresh_clone: bool) -> None:
    """A failed clone leaves a half-written directory; the next attempt would then try `git pull` on it."""
    if fresh_clone and dest.exists():
        shutil.rmtree(dest, ignore_errors=True)


def check_repo_size(repo_dir: Path) -> None:
    count = sum(1 for _ in iter_code_files(repo_dir, set(EXTENSION_TO_EXTRACTOR)))
    if count == 0:
        raise RuntimeError("No Python, JavaScript, Java, C# or PHP files found. ReArch analyzes those five languages.")
    if count > MAX_CODE_FILES:
        raise RuntimeError(f"The repository has {count} analyzable files; this server's limit is {MAX_CODE_FILES}.")


def current_commit_sha(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def main(git_url: str, work_dir: str, narrate: bool = True, narration_budget: float = 0) -> dict:
    """The whole pipeline. With narrate=False it stops short of anything that needs an LLM: the graph,
    subsystems and migration metrics are complete and final, and the prose is added afterwards by
    narrate_repo(). A server uses that so a visitor sees the result in seconds, not after Groq's rate limit."""
    work_path = Path(work_dir)
    repo_dir = work_path / "repo"
    symbols_dir = work_path / "symbols"
    graph_path = work_path / "graph.json"
    narratives_path = work_path / "narratives.json"
    narratives_cache_path = work_path / "narratives_cache.json"
    snapshots_dir = work_path / "snapshots"

    print(f"Cloning/updating {git_url} -> {repo_dir}")
    clone_or_update(git_url, repo_dir)
    check_repo_size(repo_dir)
    commit_sha = current_commit_sha(repo_dir)

    print("Extracting symbol tables...")
    extract_symbols_main(str(repo_dir), str(symbols_dir))

    print("Resolving calls/imports into graph...")
    link_calls_main(str(symbols_dir), str(graph_path))

    print("Embedding and clustering nodes into candidate subsystems...")
    cluster_nodes_main(str(graph_path), str(graph_path))

    deadline = time.monotonic() + narration_budget if narration_budget else None
    if narrate:
        print("Generating subsystem intent narratives (skipped if GROQ_API_KEY unset)...")
        generate_narratives_main(str(graph_path), str(repo_dir), str(narratives_path), str(narratives_cache_path), deadline=deadline)

        print("Building the migration plan (metrics always; written plan only if GROQ_API_KEY set)...")
        migration_plan_main(str(graph_path), str(work_path / "migration.json"), str(narratives_path), str(work_path / "migration_cache.json"), deadline=deadline)
    else:
        # An already-expired deadline makes this a cache-only pass: narratives for subsystems whose members are
        # unchanged since an earlier run are reused for free, and nothing is sent to the LLM. Without it the
        # previous run's narratives.json would be left behind, numbered for a clustering that no longer exists.
        print("Applying cached narratives (no LLM calls)...")
        generate_narratives_main(str(graph_path), str(repo_dir), str(narratives_path), str(narratives_cache_path), deadline=time.monotonic())
        print("Building the migration plan from measured facts...")
        migration_plan_main(str(graph_path), str(work_path / "migration.json"), str(narratives_path), None, use_llm=False)

    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshots_dir / f"{commit_sha}.json"
    shutil.copyfile(graph_path, snapshot_path)
    print(f"Snapshotted graph at commit {commit_sha[:12]} -> {snapshot_path}")

    return {"graph_path": graph_path, "commit_sha": commit_sha, "snapshot_path": snapshot_path}


def narrate_repo(work_dir: str, budget_seconds: float = 0, still_current=lambda: True) -> str:
    """Phase two: the prose. Returns "done", "partial" (ran out of time part-way) or "skipped".

    The written plan goes first: it is the most useful piece and one call, whereas subsystem notes are one call
    each and the free tier allows ~8K tokens a minute. Whatever time is left goes to the notes, largest
    subsystems first. `still_current` is checked before every write so that a re-analysis which started
    meanwhile (new clustering, new numbering) is never overwritten by this older run's prose.
    """
    from parser.pipeline.generate_narratives import MAX_NARRATED_CLUSTERS, take_quota_flag
    from parser.pipeline.migration_plan import analyze, check_references

    if not os.environ.get("GROQ_API_KEY"):
        return "skipped"
    take_quota_flag()  # forget anything a previous run saw
    work_path = Path(work_dir)
    graph_path = work_path / "graph.json"
    migration_path = work_path / "migration.json"
    narratives_path = work_path / "narratives.json"
    deadline = time.monotonic() + budget_seconds if budget_seconds else None

    # 1. the written plan, from the measured facts alone
    plan_path = work_path / "migration.writing.json"
    cached_notes = str(narratives_path) if narratives_path.exists() else None  # purposes already known from an earlier run
    migration_plan_main(str(graph_path), str(plan_path), cached_notes, str(work_path / "migration_cache.json"), deadline=deadline)
    written = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_path.unlink(missing_ok=True)

    # 2. subsystem notes with the time that is left
    generate_narratives_main(str(graph_path), str(work_path / "repo"), str(work_path / "narratives.writing.json"),
                             str(work_path / "narratives_cache.json"), deadline=deadline)
    notes_path = work_path / "narratives.writing.json"
    notes = json.loads(notes_path.read_text(encoding="utf-8"))
    notes_path.unlink(missing_ok=True)

    if not still_current():
        return "skipped"
    if notes:
        narratives_path.write_text(json.dumps(notes, indent=2), encoding="utf-8")
    # Rebuild the plan so each subsystem carries its inferred purpose; the prose is the one already written.
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    final = analyze(graph, notes or None)
    final["narrative"] = written.get("narrative")
    final["narrative_warnings"] = check_references(final["narrative"], final) if final["narrative"] else []
    migration_path.write_text(json.dumps(final, indent=2), encoding="utf-8")

    clusters = len({n["cluster"] for n in graph["nodes"] if n.get("cluster", -1) >= 0})
    wanted = min(clusters, MAX_NARRATED_CLUSTERS)
    if final["narrative"] and len(notes) >= wanted:
        return "done"
    if final["narrative"] or notes:
        return "partial"
    # Nothing was written: say whether that is because the day's free allowance is spent, which is useful to know.
    return "quota" if take_quota_flag() else "skipped"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: ingest_repo.py <git_url> <work_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
