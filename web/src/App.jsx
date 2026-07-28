import { useEffect, useMemo, useState, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { layoutGraph } from "./layout";
import "./App.css";

const API_BASE = "http://localhost:8000";

const KIND_COLOR = {
  module: "#64748b",
  class: "#7c3aed",
  function: "#2563eb",
  method: "#0891b2",
  unresolved: "#94a3b8",
};

const RESOLUTION_STYLE = {
  resolved: { stroke: "#16a34a", dashed: false },
  dynamic: { stroke: "#f59e0b", dashed: true },
  "inferred-http": { stroke: "#2563eb", dashed: true },
  unresolved: { stroke: "#cbd5e1", dashed: true },
};

function toFlowNode(node) {
  return {
    id: node.id,
    data: { label: node.qualified_name || node.name, raw: node },
    position: { x: 0, y: 0 },
    style: {
      background: KIND_COLOR[node.kind] || "#334155",
      color: "white",
      borderRadius: 6,
      fontSize: 11,
      padding: 6,
      width: 200,
    },
  };
}

function toFlowEdge(edge, index) {
  const style = RESOLUTION_STYLE[edge.resolution] || RESOLUTION_STYLE.unresolved;
  return {
    id: `e${index}-${edge.source}-${edge.target}`,
    source: edge.source,
    target: edge.target,
    label: edge.kind,
    animated: edge.resolution === "dynamic",
    style: { stroke: style.stroke, strokeDasharray: style.dashed ? "5,5" : undefined },
    markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
  };
}

function apiFetch(path, username, options = {}) {
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "X-User": username, "Content-Type": "application/json", ...options.headers },
  });
}

function RepoPanel({ username, setUsername, repos, selectedRepoId, onSelectRepo, onRepoAdded }) {
  const [gitUrl, setGitUrl] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setFormError(null);
    try {
      const res = await apiFetch("/api/repos", username, {
        method: "POST",
        body: JSON.stringify({ git_url: gitUrl, name: name || undefined }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const repo = await res.json();
      setGitUrl("");
      setName("");
      onRepoAdded(repo.id);
    } catch (err) {
      setFormError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="repo-panel">
      <label className="field">
        <span>Username</span>
        <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="e.g. alice" />
      </label>

      <form onSubmit={handleSubmit} className="add-repo-form">
        <label className="field">
          <span>Git URL</span>
          <input value={gitUrl} onChange={(e) => setGitUrl(e.target.value)} placeholder="https://github.com/user/repo.git" required />
        </label>
        <label className="field">
          <span>Name (optional)</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-repo" />
        </label>
        <button type="submit" disabled={submitting || !username}>
          {submitting ? "Analyzing..." : "Add repo"}
        </button>
        {formError && <p className="form-error">{formError}</p>}
      </form>

      <ul className="repo-list">
        {repos.map((repo) => (
          <li
            key={repo.id}
            className={repo.id === selectedRepoId ? "selected" : ""}
            onClick={() => repo.status === "ready" && onSelectRepo(repo.id)}
          >
            <span className={`status-dot status-${repo.status}`} />
            <span className="repo-name">{repo.name}</span>
            <span className="repo-status">{repo.status}</span>
            {repo.error && <span className="repo-error" title={repo.error}>error</span>}
          </li>
        ))}
        {repos.length === 0 && <li className="hint">No repos yet — add one above.</li>}
      </ul>
    </div>
  );
}

export default function App() {
  const [username, setUsername] = useState(() => localStorage.getItem("rearch-username") || "");
  const [repos, setRepos] = useState([]);
  const [selectedRepoId, setSelectedRepoId] = useState(null);
  const [graph, setGraph] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    localStorage.setItem("rearch-username", username);
  }, [username]);

  const refreshRepos = useCallback(() => {
    if (!username) return;
    apiFetch("/api/repos", username)
      .then((res) => res.json())
      .then(setRepos)
      .catch((err) => setError(String(err)));
  }, [username]);

  useEffect(() => {
    refreshRepos();
    setSelectedRepoId(null);
    setGraph(null);
  }, [username, refreshRepos]);

  useEffect(() => {
    if (!selectedRepoId || !username) return;
    setGraph(null);
    apiFetch(`/api/repos/${selectedRepoId}/graph`, username)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      })
      .then(setGraph)
      .catch((err) => setError(String(err)));
  }, [selectedRepoId, username]);

  const handleRepoAdded = useCallback(
    (repoId) => {
      refreshRepos();
      setSelectedRepoId(repoId);
    },
    [refreshRepos]
  );

  const { flowNodes, flowEdges } = useMemo(() => {
    if (!graph) return { flowNodes: [], flowEdges: [] };
    const rawNodes = graph.nodes.map(toFlowNode);
    const rawEdges = graph.edges.map(toFlowEdge);
    const positioned = layoutGraph(rawNodes, graph.edges);
    return { flowNodes: positioned, flowEdges: rawEdges };
  }, [graph]);

  const onNodeClick = useCallback((_, node) => setSelectedNode(node.data.raw), []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <h2>ReArch — Blueprint (dev)</h2>
        <p className="hint">Each user only ever sees their own repos. Dev-only identity: just an X-User header, no real auth.</p>
        <RepoPanel
          username={username}
          setUsername={setUsername}
          repos={repos}
          selectedRepoId={selectedRepoId}
          onSelectRepo={setSelectedRepoId}
          onRepoAdded={handleRepoAdded}
        />
      </aside>

      <div className="canvas">
        {error && <div className="status error">{error}</div>}
        {!error && !graph && <div className="status">{selectedRepoId ? "Loading blueprint..." : "Select or add a repo to view its blueprint."}</div>}
        {graph && (
          <ReactFlow nodes={flowNodes} edges={flowEdges} onNodeClick={onNodeClick} fitView>
            <Background />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        )}
      </div>

      <aside className="inspector">
        <h3>Inspector</h3>
        <p className="hint">Click a node. Dashed edges are dynamic/inferred, not certain.</p>
        {selectedNode ? (
          <dl>
            <dt>Name</dt>
            <dd>{selectedNode.qualified_name}</dd>
            <dt>Kind</dt>
            <dd>{selectedNode.kind}</dd>
            <dt>Language</dt>
            <dd>{selectedNode.language || "—"}</dd>
            <dt>Path</dt>
            <dd>{selectedNode.path || "—"}</dd>
            <dt>ID</dt>
            <dd className="mono">{selectedNode.id}</dd>
          </dl>
        ) : (
          <p className="hint">Nothing selected.</p>
        )}
        <div className="legend">
          <strong>Edges</strong>
          <div><span className="swatch" style={{ background: "#16a34a" }} /> resolved</div>
          <div><span className="swatch" style={{ background: "#f59e0b" }} /> dynamic (fan-out)</div>
          <div><span className="swatch" style={{ background: "#2563eb" }} /> inferred-http</div>
          <div><span className="swatch" style={{ background: "#cbd5e1" }} /> unresolved</div>
        </div>
      </aside>
    </div>
  );
}
