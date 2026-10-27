// Distinct palette for cluster identity (node background). Kind is shown
// separately (badge + border) so the two dimensions don't collide.
export const CLUSTER_PALETTE = [
  "#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c",
  "#0891b2", "#ca8a04", "#db2777", "#4d7c0f", "#7c3aed",
];
export const UNCLUSTERED_COLOR = "#475569";

export function clusterColor(cluster) {
  if (cluster === undefined || cluster === null || cluster < 0) return UNCLUSTERED_COLOR;
  return CLUSTER_PALETTE[cluster % CLUSTER_PALETTE.length];
}

export const READINESS_COLOR = {
  ready: "#16a34a",
  caution: "#d97706",
  "needs-prep": "#dc2626",
};
