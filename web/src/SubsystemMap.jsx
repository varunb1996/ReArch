import { useMemo } from "react";
import { Background, Controls, MarkerType, ReactFlow } from "@xyflow/react";
import { READINESS_COLOR } from "./colors";
import { layoutGraph } from "./layout";

const NODE = { width: 200, height: 78 };

// The plan as a picture: one box per subsystem, one arrow per dependency.
// "A -> B" means A depends on B, so B can be extracted first. The layout runs
// right-to-left, which puts wave 0 (depends on nothing) at the left and
// reads as extraction order, left to right.
export default function SubsystemMap({ plan, onShowSubsystem }) {
  const { nodes, edges } = useMemo(() => {
    const byId = Object.fromEntries(plan.subsystems.map((s) => [s.id, s]));
    const maxWeight = Math.max(1, ...plan.subsystems.flatMap((s) => s.depends_on.map((d) => d.weight)));

    const flowNodes = plan.subsystems.map((s) => ({
      id: `s${s.id}`,
      data: {
        id: s.id,
        label: (
          <div className="map-node">
            <div className="map-node-title">#{s.id} {s.label.split(" · ")[1] || s.label}</div>
            <div className="map-node-meta">{s.files} files · {s.loc} lines · wave {s.wave}</div>
            <div className="map-node-meta">{s.readiness}{s.in_cycle ? " · in a cycle" : ""}</div>
          </div>
        ),
      },
      position: { x: 0, y: 0 },
      style: {
        width: NODE.width,
        background: "#111827",
        color: "#e2e8f0",
        border: `2px solid ${READINESS_COLOR[s.readiness]}`,
        borderRadius: 10,
        padding: 0,
      },
    }));

    const flowEdges = plan.subsystems.flatMap((s) =>
      s.depends_on.map((d) => {
        const cyclic = s.in_cycle && byId[d.subsystem]?.in_cycle;
        const color = cyclic ? "#ef4444" : "#64748b";
        return {
          id: `e${s.id}-${d.subsystem}`,
          source: `s${s.id}`,
          target: `s${d.subsystem}`,
          style: { stroke: color, strokeWidth: 1 + (5 * d.weight) / maxWeight, opacity: cyclic ? 0.8 : 0.6 },
          markerEnd: { type: MarkerType.ArrowClosed, color },
        };
      })
    );

    const positioned = layoutGraph(flowNodes, flowEdges, { ...NODE, rankdir: "RL", ranksep: 70, nodesep: 24 });
    return { nodes: positioned, edges: flowEdges };
  }, [plan]);

  return (
    <div className="map-wrap">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        colorMode="dark"
        fitView
        fitViewOptions={{ padding: 0.08 }}
        minZoom={0.05}
        // fit again once the container has its final size; the first fit can run too early
        onInit={(instance) => requestAnimationFrame(() => instance.fitView({ padding: 0.08 }))}
        nodesDraggable={false}
        onNodeClick={(_, node) => onShowSubsystem(node.data.id)}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
      <details className="map-legend">
        <summary>How to read this map</summary>
        <div>Arrow A → B: A depends on B, so B can move first. Extraction order reads left to right.</div>
        <div><span className="swatch" style={{ background: READINESS_COLOR.ready }} /> ready
          <span className="swatch" style={{ background: READINESS_COLOR.caution }} /> caution
          <span className="swatch" style={{ background: READINESS_COLOR["needs-prep"] }} /> needs prep</div>
        <div>Thicker line = stronger coupling. Red = inside a dependency cycle. Click a box to see its code.</div>
      </details>
    </div>
  );
}
