import subprocess

import pytest

from parser.pipeline import ingest_repo
from parser.pipeline.ingest_repo import clone_or_update


def test_failed_clone_leaves_nothing_behind_and_hides_server_paths(tmp_path, monkeypatch):
    """A half-written destination would make the next attempt run `git pull` on a non-repository.

    git usually tidies up after itself, but not always (a checkout that fails midway leaves the
    tree behind), so simulate exactly that: the directory exists when the command fails.
    """
    dest = tmp_path / "work" / "repo"

    def failing_checkout(command, **kwargs):
        dest.mkdir(parents=True)
        (dest / "half-written.txt").write_text("partial", encoding="utf-8")
        raise subprocess.CalledProcessError(128, command, stderr=b"fatal: unable to checkout working tree")

    monkeypatch.setattr(ingest_repo.subprocess, "run", failing_checkout)
    with pytest.raises(RuntimeError) as excinfo:
        clone_or_update("https://github.com/o/r", dest)
    assert "Could not clone" in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)
    assert not dest.exists()


def test_an_existing_checkout_is_never_deleted_by_a_failed_update(tmp_path, monkeypatch):
    """Only a clone this call created may be discarded; a failed `git pull` must not wipe a good checkout."""
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        ingest_repo.subprocess, "run",
        lambda command, **kw: (_ for _ in ()).throw(subprocess.CalledProcessError(1, command, stderr=b"offline")),
    )
    with pytest.raises(RuntimeError):
        clone_or_update("https://github.com/o/r", dest)
    assert (dest / "keep.txt").exists()
