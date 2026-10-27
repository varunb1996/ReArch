// Three ways to run the UI:
//  - normal: talks to the ReArch API (default http://localhost:8000, or
//    VITE_API_BASE at build time);
//  - static demo (VITE_STATIC_DEMO=1 alone): no server at all. The same /api/...
//    paths are answered from pre-built JSON files under /demo/, produced by
//    scripts/export_static_demo.py, so the components do not need to know which
//    mode they are in;
//  - hybrid (VITE_STATIC_DEMO=1 plus VITE_API_BASE, what GitHub Pages serves in
//    production): the bundled examples still come from those static files, so
//    they open instantly even while the free API host is asleep, and everything
//    else (a visitor's own analyses) goes to the live API.
export const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
export const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === "1";
export const HYBRID = STATIC_DEMO && Boolean(import.meta.env.VITE_API_BASE);

const asJson = (data) =>
  Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } }));
const staticFile = (relative) => fetch(`${import.meta.env.BASE_URL}${relative}`);

const STATIC_ROUTES = [
  [/^\/api\/health$/, () => asJson({ status: "ok", public_mode: true, static_demo: true })],
  [/^\/api\/demo\/repos$/, () => staticFile("demo/index.json")],
  [/^\/api\/repos$/, () => asJson([])],
  [/^\/api\/repos\/([^/]+)\/(graph|narratives|migration)$/, (m) => staticFile(`demo/${m[1]}/${m[2]}.json`)],
  [/^\/api\/repos\/([^/]+)\/migration\.md$/, (m) => staticFile(`demo/${m[1]}/migration.md`)],
  [/^\/api\/repos\/([^/]+)\/commits$/, () => asJson([])],
];

function staticFetch(path) {
  const clean = path.split("?")[0];
  for (const [pattern, handler] of STATIC_ROUTES) {
    const match = clean.match(pattern);
    if (match) return handler(match);
  }
  return Promise.resolve(
    new Response(JSON.stringify({ detail: "Not available in the static demo. Run ReArch locally to analyze your own code." }), { status: 404 }),
  );
}

let exampleIds;
function loadExampleIds() {
  exampleIds ??= staticFile("demo/index.json")
    .then((res) => (res.ok ? res.json() : []))
    .then((rows) => new Set(rows.map((row) => row.id)))
    .catch(() => new Set());
  return exampleIds;
}

function liveFetch(path, username, options = {}) {
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "X-User": username, "Content-Type": "application/json", ...options.headers },
  });
}

async function hybridFetch(path, username, options) {
  const clean = path.split("?")[0];
  const owned = clean.match(/^\/api\/repos\/([^/]+)\//);
  if (clean === "/api/demo/repos" || (owned && (await loadExampleIds()).has(owned[1]))) return staticFetch(path);
  return liveFetch(path, username, options);
}

export function apiFetch(path, username, options = {}) {
  if (HYBRID) return hybridFetch(path, username, options);
  if (STATIC_DEMO) return staticFetch(path);
  return liveFetch(path, username, options);
}

// For endpoints that need no identity (health, the example list).
export function publicFetch(path) {
  if (HYBRID) return hybridFetch(path, "", {});
  return STATIC_DEMO ? staticFetch(path) : fetch(`${API_BASE}${path}`);
}

// A free host sleeps when idle and takes about a minute to wake; its first
// requests fail or hang meanwhile. True once the API actually answers.
export async function serverIsAwake(timeoutMs = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}/api/health`, { signal: controller.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// The API explains failures in a JSON `detail` field ("Limit of 3 analyses per
// 60 minutes reached"); show that instead of a bare "429 Too Many Requests".
export async function errorMessage(res) {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`;
}
