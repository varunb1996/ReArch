import dagre from "dagre";

const NODE_WIDTH = 200;
const NODE_HEIGHT = 44;

// options: { width, height, rankdir, nodesep, ranksep } for graphs whose nodes
// are bigger than the default symbol boxes (the subsystem map).
export function layoutGraph(nodes, edges, options = {}) {
  const { width = NODE_WIDTH, height = NODE_HEIGHT, rankdir = "LR", nodesep = 30, ranksep = 90 } = options;
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir, nodesep, ranksep });

  nodes.forEach((n) => g.setNode(n.id, { width, height }));
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: { x: pos.x - width / 2, y: pos.y - height / 2 },
    };
  });
}
