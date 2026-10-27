import { useEffect, useMemo, useState, useCallback } from "react";
import "@xyflow/react/dist/style.css";
import { API_BASE, HYBRID, STATIC_DEMO, apiFetch, errorMessage, publicFetch, serverIsAwake } from "./api";
import { INK, UNCLUSTERED_COLOR, clusterColor } from "./colors";
import CodeMap from "./CodeMap";
import MigrationPanel from "./MigrationPanel";
import SubsystemMap from "./SubsystemMap";
import "./App.css";

function RepoPanel({ liveState, showUsername, typedName, setTypedName, username, repos, demoRepos, selectedRepoId, onSelectRepo, onRepoAdded }) {
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
      if (!res.ok) throw new Error(await errorMessage(res));
      const repo = await res.json();
      setGitUrl("");
      setName("");
      onRepoAdded(repo.id);
    } catch (err) {
      setFormError(err.message || String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const row = (repo) => (
    <li key={repo.id} className={repo.id === selectedRepoId ? "selected" : ""} onClick={() => onSelectRepo(repo.id)}>
      <span className={`status-dot status-${repo.status}`} />
      <span className="repo-name">{repo.name}</span>
      <span className="repo-status">{repo.status === "pending" ? "analyzing..." : repo.status}</span>
    </li>
  );

  return (
    <div className="repo-panel">
      {showUsername && (
        <label className="field">
          <span>Username</span>
          <input value={typedName} onChange={(e) => setTypedName(e.target.value)} placeholder="e.g. alice" />
        </label>
      )}

      {liveState === "static" && (
        <div className="static-note">
          <strong>Read-only demo.</strong> These are pre-analyzed real repositories. To analyze your own code, run ReArch on your machine:{" "}
          <a href="https://github.com/varunb1996/ReArch#run-it-locally" target="_blank" rel="noreferrer">setup takes two commands</a>.
        </div>
      )}
      {liveState === "offline" && (
        <div className="static-note">
          <strong>The analysis server is not responding.</strong> The examples below still work. To analyze your own code now, run ReArch on your machine:{" "}
          <a href="https://github.com/varunb1996/ReArch#run-it-locally" target="_blank" rel="noreferrer">setup takes two commands</a>.
        </div>
      )}
      {liveState === "waking" && (
        <div className="static-note">
          <strong>Waking the analysis server.</strong> The free host sleeps when idle and needs about a minute. The examples below work meanwhile.
        </div>
      )}
      {(liveState === "ready" || liveState === "waking") && (
      <form onSubmit={handleSubmit} className="add-repo-form">
        <label className="field">
          <span>Analyze a public GitHub repo</span>
          <input value={gitUrl} onChange={(e) => setGitUrl(e.target.value)} placeholder="https://github.com/user/repo" required />
        </label>
        <label className="field">
          <span>Name (optional)</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-repo" />
        </label>
        <button type="submit" disabled={submitting || !username || liveState !== "ready"}>
          {submitting ? "Starting..." : liveState === "waking" ? "Server waking up..." : "Analyze"}
        </button>
        {formError && <p className="form-error">{formError}</p>}
      </form>
      )}

      {repos.length > 0 && (
        <>
          <div className="list-title">Your analyses{HYBRID ? " (temporary)" : ""}</div>
          <ul className="repo-list">{repos.map(row)}</ul>
        </>
      )}
      {demoRepos.length > 0 && (
        <>
          <div className="list-title">Examples (read-only)</div>
          <ul className="repo-list">{demoRepos.map(row)}</ul>
        </>
      )}
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
  const [reanalyzeError, setReanalyzeError] = useState(null);

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
    setReanalyzeError(null);
    try {
      const res = await apiFetch(`/api/repos/${repoId}/reanalyze`, username, { method: "POST" });
      if (!res.ok) throw new Error(await errorMessage(res));
      onReanalyzed();
    } catch (err) {
      setReanalyzeError(err.message || String(err));
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
      {reanalyzeError && <p className="form-error">{reanalyzeError}</p>}
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

// What a visitor sees before choosing anything: an invitation, not a blank canvas.
function EmptySheet({ liveState }) {
  return (
    <div className="empty-sheet">
      <h2 className="empty-title">Every old system has a shape. Choose a sheet to see it.</h2>
      <p className="empty-body">
        {liveState === "static"
          ? "Pick one of the five surveyed repositories on the left. Each opens as a map of its subsystems, a closer code map, and an order to move them in."
          : "Pick a surveyed repository on the left, or paste the address of a public GitHub repository to survey it yourself."}
      </p>
    </div>
  );
}

export default function App() {
  // Hosted mode asks the server (/api/health) and uses a random private id
  // instead of a typed username; local mode keeps the simple name field.
  const [publicMode, setPublicMode] = useState(null);
  const [typedName, setTypedName] = useState(() => localStorage.getItem("rearch-username") || "");
  const [visitorId] = useState(() => {
    let id = localStorage.getItem("rearch-visitor");
    if (!id) {
      id = crypto.randomUUID().replaceAll("-", "");
      localStorage.setItem("rearch-visitor", id);
    }
    return id;
  });
  const username = publicMode ? visitorId : typedName;
  // "ready" once the API answers. Only the hybrid build can be waiting: its free host sleeps when idle.
  const [serverState, setServerState] = useState(HYBRID ? "waking" : "ready");
  const liveState = !STATIC_DEMO ? "ready" : HYBRID ? serverState : "static";
  const [repos, setRepos] = useState([]);
  const [demoRepos, setDemoRepos] = useState([]);
  const [selectedRepoId, setSelectedRepoId] = useState(null);
  const [graph, setGraph] = useState(null);
  const [narratives, setNarratives] = useState({});
  const [selectedNode, setSelectedNode] = useState(null);
  const [error, setError] = useState(null);
  const [focusedCluster, setFocusedCluster] = useState(null);
  const [view, setView] = useState("map");
  const [migration, setMigration] = useState(null);
  const [showSidebar, setShowSidebar] = useState(true);
  const [showInspector, setShowInspector] = useState(() => window.innerWidth >= 1200);

  useEffect(() => {
    if (HYBRID) {
      setPublicMode(true); // examples must not wait for the sleeping server
      return undefined;
    }
    publicFetch("/api/health")
      .then((res) => res.json())
      .then((health) => setPublicMode(Boolean(health.public_mode)))
      .catch(() => {
        setPublicMode(false);
        setError(`Cannot reach the ReArch API at ${API_BASE}.`);
      });
  }, []);

  useEffect(() => {
    if (!HYBRID) return undefined;
    let cancelled = false;
    const started = Date.now();
    (async () => {
      while (!cancelled) {
        if (await serverIsAwake()) {
          setServerState("ready");
          return;
        }
        if (Date.now() - started > 150000) {
          setServerState("offline");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 4000));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    localStorage.setItem("rearch-username", typedName);
  }, [typedName]);

  const refreshRepos = useCallback(() => {
    if (publicMode === null) return;
    publicFetch("/api/demo/repos")
      .then((res) => (res.ok ? res.json() : []))
      .then(setDemoRepos)
      .catch(() => setDemoRepos([]));
    if (!username || (HYBRID && liveState !== "ready")) {
      setRepos([]);
      return;
    }
    apiFetch("/api/repos", username)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorMessage(res));
        return res.json();
      })
      .then(setRepos)
      .catch((err) => setError(err.message || String(err)));
  }, [username, publicMode, liveState]);

  useEffect(() => {
    setSelectedRepoId(null);
    setGraph(null);
  }, [username]);

  useEffect(() => {
    refreshRepos();
  }, [refreshRepos]);

  // An analysis runs in the background for minutes; poll until it settles.
  const anyPending = repos.some((r) => r.status === "pending") || demoRepos.some((r) => r.status === "pending");
  useEffect(() => {
    if (!anyPending) return undefined;
    const timer = setInterval(refreshRepos, 3000);
    return () => clearInterval(timer);
  }, [anyPending, refreshRepos]);

  const selectedRepo = [...repos, ...demoRepos].find((r) => r.id === selectedRepoId) || null;
  const selectedStatus = selectedRepo?.status;

  const loadRepoData = useCallback(() => {
    if (!selectedRepoId) return;
    apiFetch(`/api/repos/${selectedRepoId}/graph`, username)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorMessage(res));
        return res.json();
      })
      .then(setGraph)
      .catch((err) => setError(err.message || String(err)));
    apiFetch(`/api/repos/${selectedRepoId}/narratives`, username)
      .then((res) => (res.ok ? res.json() : {}))
      .then(setNarratives)
      .catch(() => setNarratives({}));
    apiFetch(`/api/repos/${selectedRepoId}/migration`, username)
      .then((res) => (res.ok ? res.json() : null))
      .then(setMigration)
      .catch(() => setMigration(null));
  }, [selectedRepoId, username]);

  useEffect(() => {
    setGraph(null);
    setNarratives({});
    setMigration(null);
    setSelectedNode(null);
    setView("map");
    setFocusedCluster(null);
    setError(null);
    if (selectedRepoId && selectedStatus === "ready") loadRepoData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRepoId, selectedStatus, username]);

  // On narrow screens the repo list gets out of the way once a repo is chosen.
  const selectRepo = useCallback((repoId) => {
    setSelectedRepoId(repoId);
    if (window.innerWidth < 900) setShowSidebar(false);
  }, []);

  const handleRepoAdded = useCallback(
    (repoId) => {
      refreshRepos();
      selectRepo(repoId);
    },
    [refreshRepos, selectRepo]
  );

  // The map and the plan need a migration plan; fall back to the graph without one.
  const activeView = !migration && view !== "blueprint" ? "blueprint" : view;

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
      <aside className={`sidebar ${showSidebar ? "" : "collapsed"}`}>
        <header className="brand">
          <img className="brand-mark" src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" width="40" height="40" />
          <div>
            <h1 className="brand-name">ReArch</h1>
            <p className="brand-tag">Decode the past. Design the future.</p>
          </div>
        </header>
        <p className="hint intro">
          {STATIC_DEMO && !HYBRID
            ? "Maps an old codebase into subsystems and an order to move them in: what to extract first, what is tangled, and how far to trust the analysis."
            : publicMode
              ? "Maps an old codebase into subsystems and an order to move them in. Your analyses are private to this browser."
              : "Local mode. Each username only sees its own repos (no real auth)."}
        </p>
        <RepoPanel
          liveState={liveState}
          showUsername={publicMode === false}
          typedName={typedName}
          setTypedName={setTypedName}
          username={username}
          repos={repos}
          demoRepos={demoRepos}
          selectedRepoId={selectedRepoId}
          onSelectRepo={selectRepo}
          onRepoAdded={handleRepoAdded}
        />
        {selectedRepo && !selectedRepo.demo && selectedRepo.status !== "pending" && (
          <HistoryPanel username={username} repoId={selectedRepoId} onReanalyzed={refreshRepos} />
        )}
        {selectedRepo?.demo && (
          <p className="hint">
            Pre-analyzed example{selectedRepo.commit ? ` (commit ${selectedRepo.commit.slice(0, 8)})` : ""}, read-only.
            {liveState === "static" ? " Run ReArch locally to analyze your own repository." : " Paste a GitHub URL above to analyze your own repository."}
          </p>
        )}
      </aside>

      <div className="canvas">
        <div className="view-tabs">
          <button className="panel-toggle" onClick={() => setShowSidebar(!showSidebar)} title="Show or hide the list of repositories">
            {showSidebar ? "Hide list" : "Repositories"}
          </button>
          {selectedRepoId && graph && (
            <>
              {migration && <button className={activeView === "map" ? "active" : ""} onClick={() => setView("map")}>Subsystem map</button>}
              <button className={activeView === "blueprint" ? "active" : ""} onClick={() => setView("blueprint")}>Code map</button>
              {migration && <button className={activeView === "migration" ? "active" : ""} onClick={() => setView("migration")}>Migration plan</button>}
            </>
          )}
          {selectedRepoId && graph && activeView === "blueprint" && (
            <button className="panel-toggle push-right" onClick={() => setShowInspector(!showInspector)}>
              {showInspector ? "Hide notes" : "Field notes"}
            </button>
          )}
        </div>
        <div className="canvas-body">
          {error && <div className="status error">{error}</div>}
          {!error && !graph && (
            <div className={selectedRepo ? "status" : "status empty"}>
              {!selectedRepo && <EmptySheet liveState={liveState} />}
              {selectedRepo?.status === "pending" && "Analyzing this repository (cloning, parsing, clustering, writing the plan). Small repositories take about a minute; larger ones can take a few minutes on the free server."}
              {selectedRepo?.status === "error" && `Analysis failed: ${selectedRepo.error || "unknown error"}`}
              {selectedRepo?.status === "ready" && "Loading the map…"}
            </div>
          )}
          {graph && activeView === "map" && (
            <SubsystemMap
              plan={migration}
              onShowSubsystem={(id) => {
                setFocusedCluster(id);
                setView("blueprint");
              }}
            />
          )}
          {graph && activeView === "migration" && (
            migration ? (
              <MigrationPanel
                plan={migration}
                repoId={selectedRepoId}
                username={username}
                onShowSubsystem={(id) => {
                  setFocusedCluster(id);
                  setView("blueprint");
                }}
              />
            ) : (
              <div className="status">No migration plan for this analysis yet. Press "Re-analyze now" to generate one.</div>
            )
          )}
          {graph && activeView === "blueprint" && (
            <CodeMap
              key={selectedRepoId}
              graph={graph}
              plan={migration}
              focusedCluster={focusedCluster}
              onFocus={setFocusedCluster}
              selectedNode={selectedNode}
              onSelectNode={setSelectedNode}
            />
          )}
        </div>
      </div>

      <aside className={`inspector ${showInspector && graph && activeView === "blueprint" ? "" : "collapsed"}`}>
        <h3>Field notes</h3>
        <p className="hint">Select a symbol on the code map. Dashed lines are guesses the analysis labels as such, not facts.</p>
        {selectedNode ? (
          <dl>
            <dt>Name</dt>
            <dd>{selectedNode.qualified_name}</dd>
            <dt>Kind</dt>
            <dd>{selectedNode.kind}</dd>
            <dt>Subsystem</dt>
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
            <strong>Why this subsystem exists</strong><span className="inferred">inferred by a model from the structure</span>
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
          <strong>Lines</strong>
          <div><span className="swatch line" style={{ background: INK }} /> a call or import the analysis resolved</div>
          <div><span className="swatch line dashed" style={{ color: "#B7791F" }} /> dynamic dispatch, any of several targets</div>
          <div><span className="swatch line dashed" style={{ color: "#3F63A8" }} /> matched by a web route string</div>
          <div><span className="swatch line dotted" style={{ color: "#8A9690" }} /> could not be resolved</div>
          <strong className="legend-gap">Symbols</strong>
          <div><span className="glyph glyph-class" /> class</div>
          <div><span className="glyph glyph-method" /> method</div>
          <div><span className="glyph glyph-function" /> function</div>
        </div>
        {clusterSummary.length > 0 && (
          <div className="legend">
            <strong>Subsystems ({clusterSummary.length}). Select one to zoom in</strong>
            {clusterSummary.map(([cluster, count]) => (
              <div
                key={cluster}
                className={`cluster-row ${focusedCluster === cluster ? "active" : ""}`}
                onClick={() => setFocusedCluster(focusedCluster === cluster ? null : cluster)}
              >
                <span className="swatch" style={{ background: clusterColor(cluster) }} /> #{cluster}, {count} symbols
              </div>
            ))}
            <div><span className="swatch" style={{ background: UNCLUSTERED_COLOR }} /> files and unassigned</div>
          </div>
        )}
      </aside>
    </div>
  );
}
