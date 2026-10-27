// The palette of a geological survey sheet. Each subsystem is a mapped "unit" with
// its own fill; readiness is carried by hatching (see App.css) as well as colour, so
// the sheet still reads in greyscale. Vermilion is reserved for faults: dependency
// cycles. Keep these in step with the custom properties in App.css.
const UNIT_PALETTE = [
  "#D9A02B", // ochre
  "#6E9A44", // moss
  "#2D8079", // teal
  "#3F63A8", // cobalt
  "#8A5BA0", // heather
  "#B5388A", // magenta
  "#A8683A", // sienna
  "#4A8FB5", // glacier
  "#8A8A2D", // olive
  "#6B4F3A", // bark
];
export const UNCLUSTERED_COLOR = "#8A9690";
export const FAULT = "#E5391B";
export const INK = "#14211E";

export function clusterColor(cluster) {
  if (cluster === undefined || cluster === null || cluster < 0) return UNCLUSTERED_COLOR;
  return UNIT_PALETTE[cluster % UNIT_PALETTE.length];
}

export const READINESS_COLOR = {
  ready: "#2F7A4B",
  caution: "#B7791F",
  "needs-prep": "#B3261E",
};

// What the plan means by each label, in plain words.
export const READINESS_TEXT = {
  ready: "ready",
  caution: "needs care",
  "needs-prep": "needs prep",
};
