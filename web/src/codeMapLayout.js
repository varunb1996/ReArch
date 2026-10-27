import { READINESS_TEXT, UNCLUSTERED_COLOR, clusterColor } from "./colors";
import { packTerritories } from "./layout";

// Turns the analysis graph into territories (one per subsystem) of symbol chips. Pure, so it can be
// checked against real graphs without a browser.

function shortDirectory(label) {
  const directory = label.split(" · ")[0];
  if (!directory || directory === "(root)") return null;
  return directory.split("/").slice(-2).join("/");
}

export function describeTerritory(key, count, plan) {
  if (key === "files") return { title: "Source files", meta: `${count} files`, color: UNCLUSTERED_COLOR };
  if (key === "outside") return { title: "Outside this codebase", meta: `${count} libraries and calls the analysis could not pin down`, color: UNCLUSTERED_COLOR };
  if (key === "loose") return { title: "Between subsystems", meta: `${count} symbols that belong to none of them`, color: UNCLUSTERED_COLOR };
  const sub = plan?.subsystems.find((s) => s.id === key);
  if (!sub) return { title: `Subsystem ${key}`, meta: `${count} symbols`, color: clusterColor(key) };
  const anchor = sub.label.split(" · ")[1];
  return {
    title: shortDirectory(sub.label) || anchor || `Subsystem ${key}`,
    meta: `#${key}, ${count} symbols, wave ${sub.wave}, ${READINESS_TEXT[sub.readiness]}${sub.in_cycle ? ", on a fault" : ""}`,
    color: clusterColor(key),
  };
}

export function layoutCodeMap(graph, plan, { showFiles, showUnresolved }) {
  const groups = new Map();
  const group = (key, node) => {
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  };
  for (const node of graph.nodes) {
    if (node.kind === "unresolved") {
      if (showUnresolved) group("outside", node);
    } else if (node.kind === "module") {
      if (showFiles) group("files", node);
    } else if (node.cluster !== undefined && node.cluster !== null && node.cluster >= 0) {
      group(node.cluster, node);
    } else {
      group("loose", node);
    }
  }

  const { territories, positions } = packTerritories([...groups].map(([id, nodes]) => ({ id, nodes })));
  const described = territories.map((t) => ({ ...t, ...describeTerritory(t.id, t.count, plan) }));

  const chips = new Map();
  for (const [key, nodes] of groups) {
    for (const node of nodes) chips.set(node.id, { raw: node, group: key, position: positions.get(node.id) });
  }
  const edges = graph.edges.filter((e) => chips.has(e.source) && chips.has(e.target));
  return { territories: described, chips, edges };
}
