import { useEffect, useMemo, useState } from "react";
import { Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow } from "@xyflow/react";
import { INK } from "./colors";
import { FitWhenReady } from "./FitWhenReady";
import { layoutCodeMap } from "./codeMapLayout";
import { CHIP } from "./layout";

// The code map: every subsystem is a territory holding its own functions, classes and methods.
// Lines drawn between territories are the coupling the migration plan is about; lines inside a
// territory are hidden until asked for, because 700 symbols with 2,400 links is a hairball.

const EDGE_STYLE = {
  resolved: { stroke: INK, dash: undefined, opacity: 0.4 },
  dynamic: { stroke: "#B7791F", dash: "7 4", opacity: 0.75 },
  "inferred-http": { stroke: "#3F63A8", dash: "7 4", opacity: 0.75 },
  unresolved: { stroke: "#8A9690", dash: "2 5", opacity: 0.55 },
};

function Territory({ data }) {
  return (
    <div className={`territory ${data.dimmed ? "dimmed" : ""} ${data.focused ? "focused" : ""}`} style={{ "--unit": data.color }}>
      <div className="territory-title">{data.title}</div>
      <div className="territory-meta">{data.meta}</div>
    </div>
  );
}

function Chip({ data }) {
  return (
    <div
      className={`chip chip-${data.kind} ${data.dimmed ? "dimmed" : ""} ${data.match ? "match" : ""} ${data.selected ? "selected" : ""}`}
      style={{ "--unit": data.color }}
      title={`${data.kind}: ${data.name}`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span className="chip-glyph" aria-hidden="true" />
      <span className="chip-name">{data.name}</span>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

const nodeTypes = { territory: Territory, chip: Chip };

export default function CodeMap({ graph, plan, focusedCluster, onFocus, selectedNode, onSelectNode }) {
  const [query, setQuery] = useState("");
  const [showFiles, setShowFiles] = useState(false);
  const [showUnresolved, setShowUnresolved] = useState(false);
  const [showInternal, setShowInternal] = useState(false);
  const [instance, setInstance] = useState(null);

  const layout = useMemo(() => layoutCodeMap(graph, plan, { showFiles, showUnresolved }), [graph, plan, showFiles, showUnresolved]);
  const needle = query.trim().toLowerCase();
  const selectedId = selectedNode?.id ?? null;

  const { nodes, edges, matches } = useMemo(() => {
    let matched = 0;
    const territoryNodes = layout.territories.map((t) => ({
      id: `t:${t.id}`,
      type: "territory",
      data: { title: t.title, meta: t.meta, color: t.color, dimmed: focusedCluster !== null && t.id !== focusedCluster, focused: focusedCluster !== null && t.id === focusedCluster },
      position: { x: t.x, y: t.y },
      width: t.width,
      height: t.height,
      draggable: false,
      selectable: false,
      focusable: false,
      zIndex: -1,
    }));

    const chipNodes = [];
    for (const [id, chip] of layout.chips) {
      const name = chip.raw.qualified_name || chip.raw.name;
      const match = Boolean(needle) && name.toLowerCase().includes(needle);
      if (match) matched += 1;
      const territory = layout.territories.find((t) => t.id === chip.group);
      chipNodes.push({
        id,
        type: "chip",
        data: {
          name,
          kind: chip.raw.kind,
          raw: chip.raw,
          color: territory?.color,
          match,
          selected: id === selectedId,
          dimmed: (Boolean(needle) && !match) || (focusedCluster !== null && chip.group !== focusedCluster),
        },
        position: chip.position,
        width: CHIP.width,
        height: CHIP.height,
        draggable: false,
      });
    }

    const flowEdges = [];
    layout.edges.forEach((edge, index) => {
      const from = layout.chips.get(edge.source);
      const to = layout.chips.get(edge.target);
      const touchesSelection = selectedId && (edge.source === selectedId || edge.target === selectedId);
      if (!(from.group !== to.group || showInternal || touchesSelection)) return;
      const style = EDGE_STYLE[edge.resolution] || EDGE_STYLE.unresolved;
      const touchesFocus = focusedCluster === null || from.group === focusedCluster || to.group === focusedCluster;
      let opacity = style.opacity;
      if (selectedId) opacity = touchesSelection ? 1 : 0.07;
      else if (!touchesFocus) opacity = 0.06;
      flowEdges.push({
        id: `e${index}`,
        source: edge.source,
        target: edge.target,
        style: { stroke: style.stroke, strokeDasharray: style.dash, strokeWidth: touchesSelection ? 2.2 : 1.1, opacity },
        markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke, width: 9, height: 9, markerUnits: "userSpaceOnUse" },
        zIndex: touchesSelection ? 5 : 0,
      });
    });
    return { nodes: [...territoryNodes, ...chipNodes], edges: flowEdges, matches: matched };
  }, [layout, needle, focusedCluster, selectedId, showInternal]);

  // Zoom to the focused subsystem; zoom back out when the focus is cleared.
  useEffect(() => {
    if (!instance) return;
    const target = focusedCluster === null ? null : layout.territories.find((t) => t.id === focusedCluster);
    if (target) instance.fitBounds({ x: target.x, y: target.y, width: target.width, height: target.height }, { padding: 0.3, duration: 550, maxZoom: 1.2 });
  }, [instance, focusedCluster, layout]);

  const focusedTitle = focusedCluster === null ? null : layout.territories.find((t) => t.id === focusedCluster)?.title;

  return (
    <div className="codemap">
      <div className="codemap-toolbar">
        <input
          className="search-box"
          type="search"
          aria-label="Find a symbol"
          placeholder="Find a function, class or method"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {needle && <span className="toolbar-count">{matches} found</span>}
        <label className="toggle"><input type="checkbox" checked={showInternal} onChange={(e) => setShowInternal(e.target.checked)} /> links inside subsystems</label>
        <label className="toggle"><input type="checkbox" checked={showFiles} onChange={(e) => setShowFiles(e.target.checked)} /> source files</label>
        <label className="toggle"><input type="checkbox" checked={showUnresolved} onChange={(e) => setShowUnresolved(e.target.checked)} /> outside the codebase</label>
        {focusedTitle && (
          <button className="clear-focus" onClick={() => onFocus(null)}>
            Showing {focusedTitle}. Show everything
          </button>
        )}
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => node.type === "chip" && onSelectNode(node.data.raw)}
        onPaneClick={() => onSelectNode(null)}
        onInit={setInstance}
        colorMode="light"
        minZoom={0.05}
        maxZoom={1.8}
        nodesDraggable={false}
        nodesConnectable={false}
        onlyRenderVisibleElements
      >
        <Background variant={BackgroundVariant.Cross} gap={64} size={9} lineWidth={1} color="rgba(20, 33, 30, 0.14)" />
        <Controls showInteractive={false} />
        {focusedCluster === null && <FitWhenReady padding={0.06} trigger={layout} />}
        <MiniMap
          pannable
          zoomable
          style={{ width: 170, height: 110 }}
          nodeStrokeWidth={0}
          nodeColor={(n) => (n.type === "territory" ? n.data.color : "transparent")}
          maskColor="rgba(227, 232, 228, 0.75)"
        />
      </ReactFlow>
    </div>
  );
}
