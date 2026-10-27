"""Drive a running ReArch API like a visitor does, and fail loudly if anything is off.

    python scripts/smoke_hosted.py http://localhost:10000 https://github.com/spring-projects/spring-petclinic
    python scripts/smoke_hosted.py https://rearch-api.onrender.com https://github.com/slimphp/Slim

Waits for the server to wake (a free host sleeps when idle), analyzes each repository,
and checks the results: the analysis finished, the graph has symbols in the expected
language, subsystems exist, and the migration plan was produced. Standard library only.
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

EXPECTED_LANGUAGE = {"petclinic": "java", "eshoponweb": "csharp", "slim": "php", "requests": "python"}


def call(base: str, path: str, visitor: str | None = None, body: dict | None = None, timeout: int = 60):
    request = urllib.request.Request(
        f"{base}{path}",
        method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"X-User": visitor} if visitor else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"FAIL {request.get_method()} {path} -> HTTP {exc.code}: {exc.read()[:300]!r}") from None


def wait_for_server(base: str, give_up_after: int = 420) -> None:
    started = time.time()
    while True:
        try:
            if call(base, "/api/health", timeout=15).get("status") == "ok":
                print(f"server ready after {time.time() - started:.0f}s")
                return
        except (SystemExit, OSError, urllib.error.URLError):
            pass
        if time.time() - started > give_up_after:
            raise SystemExit(f"FAIL server did not answer /api/health within {give_up_after}s")
        time.sleep(5)


def analyze(base: str, visitor: str, url: str, give_up_after: int = 900) -> None:
    started = time.time()
    repo = call(base, "/api/repos", visitor, {"git_url": url})
    while True:
        state = next(r for r in call(base, "/api/repos", visitor) if r["id"] == repo["id"])
        if state["status"] != "pending":
            break
        if time.time() - started > give_up_after:
            raise SystemExit(f"FAIL {url}: still analyzing after {give_up_after}s")
        time.sleep(3)
    elapsed = time.time() - started
    if state["status"] != "ready":
        raise SystemExit(f"FAIL {url}: analysis ended as {state['status']}: {state.get('error')}")
    graph = call(base, f"/api/repos/{repo['id']}/graph", visitor)
    plan = call(base, f"/api/repos/{repo['id']}/migration", visitor)
    languages = {n["language"] for n in graph["nodes"] if n.get("language")}
    name = url.rstrip("/").rsplit("/", 1)[-1].lower()
    expected = EXPECTED_LANGUAGE.get(name)
    problems = []
    if expected and expected not in languages:
        problems.append(f"expected {expected} symbols, found languages {sorted(languages)}")
    if not plan["summary"]["subsystems"]:
        problems.append("no subsystems were found")
    if not graph["edges"]:
        problems.append("the graph has no edges")
    if problems:
        raise SystemExit(f"FAIL {url}: " + "; ".join(problems))
    print(f"ok  {name:<18} {elapsed:6.1f}s  {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
          f"{plan['summary']['subsystems']} subsystems, {plan['summary']['waves']} waves, languages {sorted(languages)}")


def main(base: str, urls: list[str]) -> None:
    base = base.rstrip("/")
    wait_for_server(base)
    visitor = uuid.uuid4().hex  # a fresh private id, exactly what a browser generates
    for url in urls:
        analyze(base, visitor, url)
    print("all checks passed")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1], sys.argv[2:])
