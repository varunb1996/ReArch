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

export default function App() {
  const [graph, setGraph] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("http://localhost:8000/api/graph")
      .then((res) => res.json())
      .then(setGraph)
      .catch((err) => setError(String(err)));
  }, []);

  const { flowNodes, flowEdges } = useMemo(() => {
    if (!graph) return { flowNodes: [], flowEdges: [] };
    const rawNodes = graph.nodes.map(toFlowNode);
    const rawEdges = graph.edges.map(toFlowEdge);
    const positioned = layoutGraph(rawNodes, graph.edges);
    return { flowNodes: positioned, flowEdges: rawEdges };
  }, [graph]);

  const onNodeClick = useCallback((_, node) => setSelected(node.data.raw), []);

  if (error) return <div className="status">Failed to load graph: {error}. Is the API running on :8000?</div>;
  if (!graph) return <div className="status">Loading blueprint...</div>;

  return (
    <div className="app-shell">
      <div className="canvas">
        <ReactFlow nodes={flowNodes} edges={flowEdges} onNodeClick={onNodeClick} fitView>
          <Background />
          <Controls />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
      <aside className="inspector">
        <h2>ReArch — Blueprint (dev)</h2>
        <p className="hint">Click a node to inspect it. Dashed edges are dynamic/inferred, not certain.</p>
        {selected ? (
          <dl>
            <dt>Name</dt>
            <dd>{selected.qualified_name}</dd>
            <dt>Kind</dt>
            <dd>{selected.kind}</dd>
            <dt>Language</dt>
            <dd>{selected.language || "—"}</dd>
            <dt>Path</dt>
            <dd>{selected.path || "—"}</dd>
            <dt>ID</dt>
            <dd className="mono">{selected.id}</dd>
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
