// Two ways to run the UI:
//  - normal: talks to the ReArch API (default http://localhost:8000, or
//    VITE_API_BASE at build time);
//  - static demo (VITE_STATIC_DEMO=1, what GitHub Pages serves): no server at
//    all. The same /api/... paths are answered from pre-built JSON files under
//    /demo/, produced by scripts/export_static_demo.py, so the components do
//    not need to know which mode they are in.
export const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
export const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === "1";

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

export function apiFetch(path, username, options = {}) {
  if (STATIC_DEMO) return staticFetch(path);
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "X-User": username, "Content-Type": "application/json", ...options.headers },
  });
}

// For endpoints that need no identity (health, the example list).
export function publicFetch(path) {
  return STATIC_DEMO ? staticFetch(path) : fetch(`${API_BASE}${path}`);
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
