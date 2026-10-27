import { useMemo } from "react";
import { FitWhenReady } from "./FitWhenReady";
import { Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow } from "@xyflow/react";
import { FAULT, INK, READINESS_TEXT, clusterColor } from "./colors";
import { TILE, layoutWaves } from "./layout";

// The plan as a survey sheet. One column per extraction wave, wave 0 on the left, so reading
// left to right is reading the order of extraction. An arrow A -> B means A depends on B, so
// B moves first; arrows therefore point left. Hatching says how ready a unit is, and a
// vermilion dashed line is a fault: a dependency cycle that has to be cut before anything moves.

function shortDirectory(label) {
  const directory = label.split(" · ")[0];
  if (!directory || directory === "(root)") return null;
  return directory.split("/").slice(-2).join("/");
}

function Unit({ data }) {
  return (
    <div className={`unit ${data.readiness}`} style={{ "--unit": data.color, "--delay": `${data.delay}ms` }}>
      <Handle type="target" position={Position.Right} isConnectable={false} />
      <Handle type="source" position={Position.Left} isConnectable={false} />
      <div className="unit-hatch" aria-hidden="true" />
      <div className="unit-body">
        <div className="unit-title">
          <span className="unit-id">{data.id}</span>
          {data.title}
        </div>
        {data.anchor && <div className="unit-anchor">around {data.anchor}</div>}
        <div className="unit-meta">
          {data.files} {data.files === 1 ? "file" : "files"}, {data.loc} lines
        </div>
        <div className="unit-flags">
          {READINESS_TEXT[data.readiness]}
          {data.inCycle ? ", on a fault" : ""}
        </div>
      </div>
    </div>
  );
}

function Stratum({ data }) {
  return (
    <div className="stratum" style={{ height: data.height, width: data.width, "--delay": `${data.delay}ms` }}>
      <div className="stratum-head">
        <div className="stratum-name">Wave {data.wave}</div>
        <div className="stratum-hint">{data.wave === 0 ? "depends on nothing, move first" : `needs waves 0 to ${data.wave - 1}`}</div>
      </div>
    </div>
  );
}

const nodeTypes = { unit: Unit, stratum: Stratum };

export default function SubsystemMap({ plan, onShowSubsystem }) {
  const { nodes, edges } = useMemo(() => {
    const byId = Object.fromEntries(plan.subsystems.map((s) => [s.id, s]));
    const maxWeight = Math.max(1, ...plan.subsystems.flatMap((s) => s.depends_on.map((d) => d.weight)));
    const { positions, columns } = layoutWaves(plan.subsystems);

    const strata = columns.map((c) => ({
      id: `wave${c.wave}`,
      type: "stratum",
      data: { wave: c.wave, height: c.height, width: c.width, delay: c.wave * 170 },
      position: { x: c.x, y: 0 },
      width: c.width,
      height: c.height,
      draggable: false,
      selectable: false,
      focusable: false,
      zIndex: -1,
    }));

    const units = plan.subsystems.map((s) => {
      const anchor = s.label.split(" · ")[1] || null;
      const directory = shortDirectory(s.label);
      return {
        id: `s${s.id}`,
        type: "unit",
        data: {
          id: s.id,
          title: directory || anchor || `Subsystem ${s.id}`,
          anchor: directory ? anchor : null,
          files: s.files,
          loc: s.loc,
          readiness: s.readiness,
          inCycle: s.in_cycle,
          color: clusterColor(s.id),
          delay: s.wave * 170 + 120 + (positions[s.id].y % 7) * 18,
        },
        position: positions[s.id],
        width: TILE.width,
        height: TILE.height,
        draggable: false,
      };
    });

    const links = plan.subsystems.flatMap((s) =>
      s.depends_on.map((d) => {
        const fault = s.in_cycle && byId[d.subsystem]?.in_cycle;
        const color = fault ? FAULT : INK;
        return {
          id: `e${s.id}-${d.subsystem}`,
          source: `s${s.id}`,
          target: `s${d.subsystem}`,
          className: fault ? "link-fault" : "link-dependency",
          style: { stroke: color, strokeWidth: 1.2 + (4.5 * d.weight) / maxWeight, opacity: fault ? 0.9 : 0.5 },
          markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14, markerUnits: "userSpaceOnUse" },
        };
      })
    );
    return { nodes: [...strata, ...units], edges: links };
  }, [plan]);

  return (
    <div className="map-wrap">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode="light"
        minZoom={0.1}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        onNodeClick={(_, node) => node.type === "unit" && onShowSubsystem(node.data.id)}
      >
        <Background variant={BackgroundVariant.Cross} gap={64} size={9} lineWidth={1} color="rgba(20, 33, 30, 0.16)" />
        <Controls showInteractive={false} />
        <FitWhenReady padding={0.1} trigger={plan} />
      </ReactFlow>
      <aside className="map-key" aria-label="Map key">
        <div className="key-title">Key</div>
        <div className="key-row"><span className="key-swatch ready" /> ready to extract</div>
        <div className="key-row"><span className="key-swatch caution" /> needs care</div>
        <div className="key-row"><span className="key-swatch needs-prep" /> needs preparation first</div>
        <div className="key-row"><span className="key-line" /> depends on, thicker is tighter</div>
        <div className="key-row"><span className="key-line fault" /> fault: a dependency cycle</div>
        <div className="key-note">Select a unit to open its code.</div>
      </aside>
    </div>
  );
}
