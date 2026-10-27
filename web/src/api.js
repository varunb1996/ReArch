// Set VITE_API_BASE at build time to point the UI at a deployed backend
// (e.g. the Hugging Face Space). Defaults to the local dev server.
export const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");

export function apiFetch(path, username, options = {}) {
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "X-User": username, "Content-Type": "application/json", ...options.headers },
  });
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
