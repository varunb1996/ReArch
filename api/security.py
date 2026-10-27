"""Guardrails for exposing ReArch to the public internet.

Local development stays permissive. Setting REARCH_PUBLIC_MODE=1 (the Docker
image does) turns on: GitHub-only repo URLs, unguessable per-visitor
identities, a read-only shared "demo" account, per-user and per-IP rate
limits, and one analysis at a time so a small free server doesn't fall over.
"""
import os
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager

from fastapi import HTTPException, Request

DEMO_USER = "demo"
GITHUB_REPO_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(?:\.git)?/?")
VISITOR_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")


def public_mode() -> bool:
    return os.environ.get("REARCH_PUBLIC_MODE") == "1"


def validate_git_url(url: str) -> str:
    """Reject anything that is not a plain GitHub repo URL (public mode), and
    anything git could mistake for an option (always)."""
    url = url.strip()
    if not url or url.startswith("-") or "\n" in url or "\x00" in url:
        raise HTTPException(400, "Invalid git URL.")
    if public_mode() and (".." in url or not GITHUB_REPO_URL.fullmatch(url)):
        raise HTTPException(400, "This server only analyzes public https://github.com/<owner>/<repo> repositories.")
    return url


def validate_identity(username: str) -> None:
    """Public mode treats the X-User header as a bearer secret: a long random
    id the browser generated. Short guessable names like "alice" are refused,
    so one visitor can't read another's analyses by guessing a username."""
    if public_mode() and username != DEMO_USER and not VISITOR_ID.fullmatch(username):
        raise HTTPException(401, "Invalid visitor id.")


class RateLimiter:
    """Sliding-window limiter, in memory (resets on restart, which is fine here)."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            hits = self.hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                minutes = max(1, self.window // 60)
                raise HTTPException(429, f"Limit of {self.limit} analyses per {minutes} minutes reached. Try again later.")
            hits.append(now)


user_limiter = RateLimiter(limit=3, window_seconds=3600)
ip_limiter = RateLimiter(limit=10, window_seconds=86400)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def enforce_analysis_limits(user_id: str, request: Request) -> None:
    if public_mode():
        user_limiter.check(user_id)
        ip_limiter.check(client_ip(request))


_slot = threading.Semaphore(1)


def try_acquire_slot() -> bool:
    """Claim the single analysis slot for a background job (released by the job)."""
    return _slot.acquire(blocking=False)


def release_slot() -> None:
    _slot.release()


@contextmanager
def analysis_slot(wait: bool = False):
    """One analysis at a time. Interactive requests fail fast with 503; the
    webhook waits its turn because GitHub is not a person who can retry."""
    acquired = _slot.acquire(timeout=900) if wait else _slot.acquire(blocking=False)
    if not acquired:
        raise HTTPException(503, "Another analysis is running on this small server. Try again in a minute.")
    try:
        yield
    finally:
        _slot.release()
