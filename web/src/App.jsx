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

const KIND_BADGE = {
  module: "mod",
  class: "cls",
  function: "fn",
  method: "mth",
  unresolved: "?",
};

const KIND_BORDER = {
  module: "#e2e8f0",
  class: "#f8fafc",
  function: "#e2e8f0",
  method: "#e2e8f0",
  unresolved: "#64748b",
};

// Distinct, colorblind-friendlier palette for cluster identity (background).
// Kind is shown separately (badge + border) so the two dimensions don't collide.
const CLUSTER_PALETTE = [
  "#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c",
  "#0891b2", "#ca8a04", "#db2777", "#4d7c0f", "#7c3aed",
];
const UNCLUSTERED_COLOR = "#475569";

const RESOLUTION_STYLE = {
  resolved: { stroke: "#16a34a", dashed: false },
  dynamic: { stroke: "#f59e0b", dashed: true },
  "inferred-http": { stroke: "#2563eb", dashed: true },
  unresolved: { stroke: "#cbd5e1", dashed: true },
};

function clusterColor(cluster) {
  if (cluster === undefined || cluster === null || cluster < 0) return UNCLUSTERED_COLOR;
  return CLUSTER_PALETTE[cluster % CLUSTER_PALETTE.length];
}

function toFlowNode(node) {
  const background = node.kind === "module" || node.kind === "unresolved" ? UNCLUSTERED_COLOR : clusterColor(node.cluster);
  return {
    id: node.id,
    data: { label: `[${KIND_BADGE[node.kind] || node.kind}] ${node.qualified_name || node.name}`, raw: node },
    position: { x: 0, y: 0 },
    style: {
      background,
      color: "white",
      borderRadius: 6,
      fontSize: 11,
      padding: "4px 6px",
      width: 200,
      border: `1px solid ${KIND_BORDER[node.kind] || "#e2e8f0"}`,
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

function HistoryPanel({ username, repoId, onReanalyzed }) {
  const [commits, setCommits] = useState([]);
  const [fromCommit, setFromCommit] = useState("");
  const [toCommit, setToCommit] = useState("");
  const [diff, setDiff] = useState(null);
  const [diffError, setDiffError] = useState(null);
  const [reanalyzing, setReanalyzing] = useState(false);

  const refreshCommits = useCallback(() => {
    apiFetch(`/api/repos/${repoId}/commits`, username)
      .then((res) => res.json())
      .then((list) => {
        setCommits(list);
        if (list.length >= 2) {
          setToCommit(list[0].commit_sha);
          setFromCommit(list[1].commit_sha);
        }
      })
      .catch(() => setCommits([]));
  }, [repoId, username]);

  useEffect(() => {
    refreshCommits();
    setDiff(null);
    setDiffError(null);
  }, [refreshCommits]);

  const handleReanalyze = async () => {
    setReanalyzing(true);
    try {
      await apiFetch(`/api/repos/${repoId}/reanalyze`, username, { method: "POST" });
      refreshCommits();
      onReanalyzed();
    } finally {
      setReanalyzing(false);
    }
  };

  const handleViewDiff = async () => {
    setDiffError(null);
    setDiff(null);
    try {
      const res = await apiFetch(`/api/repos/${repoId}/diff?from_commit=${fromCommit}&to_commit=${toCommit}`, username);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setDiff(await res.json());
    } catch (err) {
      setDiffError(String(err));
    }
  };

  return (
    <div className="history-panel">
      <div className="history-header">
        <strong>History ({commits.length} analyzed commit{commits.length === 1 ? "" : "s"})</strong>
        <button onClick={handleReanalyze} disabled={reanalyzing}>
          {reanalyzing ? "Re-analyzing..." : "Re-analyze now"}
        </button>
      </div>
      {commits.length >= 2 ? (
        <div className="diff-controls">
          <label className="field">
            <span>From</span>
            <select value={fromCommit} onChange={(e) => setFromCommit(e.target.value)}>
              {commits.map((c) => (
                <option key={c.commit_sha} value={c.commit_sha}>{c.commit_sha.slice(0, 10)}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>To</span>
            <select value={toCommit} onChange={(e) => setToCommit(e.target.value)}>
              {commits.map((c) => (
                <option key={c.commit_sha} value={c.commit_sha}>{c.commit_sha.slice(0, 10)}</option>
              ))}
            </select>
          </label>
          <button onClick={handleViewDiff}>View diff</button>
        </div>
      ) : (
        <p className="hint">Re-analyze again after new commits land to enable diffing.</p>
      )}
      {diffError && <p className="form-error">{diffError}</p>}
      {diff && (
        <div className="diff-result">
          <p className="hint">
            +{diff.summary.nodes_added} -{diff.summary.nodes_removed} nodes, +{diff.summary.edges_added} -{diff.summary.edges_removed} edges,{" "}
            {diff.summary.resolution_changed} resolution changes, {diff.summary.cluster_membership_changed} cluster moves
          </p>
          {diff.nodes_added.length > 0 && (
            <details open>
              <summary>Nodes added ({diff.nodes_added.length})</summary>
              {diff.nodes_added.map((n) => (
                <div key={n.id} className="diff-item added">+ {n.qualified_name} ({n.path})</div>
              ))}
            </details>
          )}
          {diff.nodes_removed.length > 0 && (
            <details open>
              <summary>Nodes removed ({diff.nodes_removed.length})</summary>
              {diff.nodes_removed.map((n) => (
                <div key={n.id} className="diff-item removed">- {n.qualified_name} ({n.path})</div>
              ))}
            </details>
          )}
          {diff.cluster_membership_changed.length > 0 && (
            <details>
              <summary>Cluster moves ({diff.cluster_membership_changed.length})</summary>
              {diff.cluster_membership_changed.map((c) => (
                <div key={c.id} className="diff-item">{c.qualified_name}: #{c.old_cluster} &rarr; #{c.new_cluster}</div>
              ))}
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [username, setUsername] = useState(() => localStorage.getItem("rearch-username") || "");
  const [repos, setRepos] = useState([]);
  const [selectedRepoId, setSelectedRepoId] = useState(null);
  const [graph, setGraph] = useState(null);
  const [narratives, setNarratives] = useState({});
  const [selectedNode, setSelectedNode] = useState(null);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [focusedCluster, setFocusedCluster] = useState(null);

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

  const loadRepoData = useCallback(() => {
    if (!selectedRepoId || !username) return;
    apiFetch(`/api/repos/${selectedRepoId}/graph`, username)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      })
      .then(setGraph)
      .catch((err) => setError(String(err)));
    apiFetch(`/api/repos/${selectedRepoId}/narratives`, username)
      .then((res) => (res.ok ? res.json() : {}))
      .then(setNarratives)
      .catch(() => setNarratives({}));
  }, [selectedRepoId, username]);

  useEffect(() => {
    if (!selectedRepoId || !username) return;
    setGraph(null);
    setNarratives({});
    setSearchQuery("");
    setFocusedCluster(null);
    loadRepoData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

    const query = searchQuery.trim().toLowerCase();
    const styled = positioned.map((n) => {
      const matchesSearch = !query || n.data.label.toLowerCase().includes(query);
      const matchesCluster = focusedCluster === null || n.data.raw.cluster === focusedCluster;
      const dimmed = !matchesSearch || !matchesCluster;
      return { ...n, style: { ...n.style, opacity: dimmed ? 0.12 : 1 } };
    });

    return { flowNodes: styled, flowEdges: rawEdges };
  }, [graph, searchQuery, focusedCluster]);

  const onNodeClick = useCallback((_, node) => setSelectedNode(node.data.raw), []);

  const clusterSummary = useMemo(() => {
    if (!graph) return [];
    const counts = new Map();
    for (const node of graph.nodes) {
      if (node.cluster === undefined || node.cluster === null || node.cluster < 0) continue;
      counts.set(node.cluster, (counts.get(node.cluster) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => a[0] - b[0]);
  }, [graph]);

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
        {selectedRepoId && (
          <HistoryPanel
            username={username}
            repoId={selectedRepoId}
            onReanalyzed={() => {
              refreshRepos();
              loadRepoData();
            }}
          />
        )}
      </aside>

      <div className="canvas">
        {error && <div className="status error">{error}</div>}
        {!error && !graph && <div className="status">{selectedRepoId ? "Loading blueprint..." : "Select or add a repo to view its blueprint."}</div>}
        {graph && (
          <>
            <div className="canvas-toolbar">
              <input
                className="search-box"
                type="text"
                placeholder="Search nodes by name..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {focusedCluster !== null && (
                <button className="clear-focus" onClick={() => setFocusedCluster(null)}>
                  Clear cluster focus (#{focusedCluster})
                </button>
              )}
            </div>
            <ReactFlow nodes={flowNodes} edges={flowEdges} onNodeClick={onNodeClick} fitView>
              <Background />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
          </>
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
            <dt>Cluster</dt>
            <dd>{selectedNode.cluster >= 0 ? `#${selectedNode.cluster}` : "unclustered"}</dd>
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
        {selectedNode && selectedNode.cluster >= 0 && narratives[selectedNode.cluster] && (
          <div className="narrative">
            <strong>Why this subsystem exists (inferred)</strong>
            <p>{narratives[selectedNode.cluster].text}</p>
            <details>
              <summary>Grounded in</summary>
              <p className="hint">
                Calls out to: {narratives[selectedNode.cluster].grounded_in.calls_out.join(", ") || "none detected"}
              </p>
              <p className="hint">
                Called by: {narratives[selectedNode.cluster].grounded_in.calls_in.join(", ") || "none detected"}
              </p>
              <p className="hint">Excerpt shown to the model: {narratives[selectedNode.cluster].grounded_in.excerpt_source}</p>
            </details>
          </div>
        )}
        <div className="legend">
          <strong>Edges</strong>
          <div><span className="swatch" style={{ background: "#16a34a" }} /> resolved</div>
          <div><span className="swatch" style={{ background: "#f59e0b" }} /> dynamic (fan-out)</div>
          <div><span className="swatch" style={{ background: "#2563eb" }} /> inferred-http</div>
          <div><span className="swatch" style={{ background: "#cbd5e1" }} /> unresolved</div>
        </div>
        {clusterSummary.length > 0 && (
          <div className="legend">
            <strong>Clusters ({clusterSummary.length}) — click to focus</strong>
            {clusterSummary.map(([cluster, count]) => (
              <div
                key={cluster}
                className={`cluster-row ${focusedCluster === cluster ? "active" : ""}`}
                onClick={() => setFocusedCluster(focusedCluster === cluster ? null : cluster)}
              >
                <span className="swatch" style={{ background: clusterColor(cluster) }} /> #{cluster} ({count} nodes)
              </div>
            ))}
            <div><span className="swatch" style={{ background: UNCLUSTERED_COLOR }} /> unclustered / modules</div>
          </div>
        )}
      </aside>
    </div>
  );
}
