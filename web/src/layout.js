// Two layouts, both deterministic and both shaped by what the picture has to say.
//
// layoutWaves: the subsystem map. One column per extraction wave, wave 0 on the left,
// so reading left to right is reading the order of extraction.
//
// packTerritories: the code map. Every subsystem is a territory holding its own symbols
// in a grid, and territories are packed into rows. A general graph layout (dagre) was
// tried first: on a 768-node graph it stacked everything into one thin column, and
// focusing a subsystem then scattered its members along it.

export const TILE = { width: 236, height: 118 };

// A tall wave wraps into side-by-side sub-columns so the sheet fills a landscape window instead of being
// scaled down to fit one very tall column. The wrap is chosen by simulation: whichever row limit gives the
// largest picture in a typical 3:2 window.
function chooseRowLimit(counts, { colGap, rowGap, subGap, headerHeight }) {
  const tallest = Math.max(1, ...counts);
  let best = { rows: tallest, scale: -1 };
  for (let rows = 2; rows <= Math.max(2, tallest); rows += 1) {
    const subColumns = counts.map((n) => Math.ceil(n / rows));
    const width = subColumns.reduce((sum, c) => sum + c * TILE.width + (c - 1) * subGap, 0) + (counts.length - 1) * colGap;
    const height = headerHeight + Math.min(rows, tallest) * TILE.height + (Math.min(rows, tallest) - 1) * rowGap;
    const scale = Math.min(1500 / width, 1000 / height);
    if (scale > best.scale + 1e-9) best = { rows, scale };
  }
  return best.rows;
}

export function layoutWaves(subsystems, { colGap = 130, rowGap = 20, subGap = 44, headerHeight = 78 } = {}) {
  const byWave = new Map();
  for (const sub of subsystems) {
    if (!byWave.has(sub.wave)) byWave.set(sub.wave, []);
    byWave.get(sub.wave).push(sub);
  }
  const waves = [...byWave.keys()].sort((a, b) => a - b);
  const rowLimit = chooseRowLimit(waves.map((w) => byWave.get(w).length), { colGap, rowGap, subGap, headerHeight });
  const rowsIn = (n) => Math.min(rowLimit, n);
  const tallestRows = Math.max(1, ...waves.map((w) => rowsIn(byWave.get(w).length)));
  const bodyHeight = tallestRows * TILE.height + (tallestRows - 1) * rowGap;

  const positions = {};
  const columns = [];
  let x = 0;
  for (const wave of waves) {
    // cycles sit together, biggest first
    const list = [...byWave.get(wave)].sort((a, b) => Number(b.in_cycle) - Number(a.in_cycle) || b.loc - a.loc || a.id - b.id);
    const subColumns = Math.ceil(list.length / rowLimit);
    list.forEach((sub, i) => {
      const column = Math.floor(i / rowLimit);
      const inColumn = Math.min(rowLimit, list.length - column * rowLimit);
      const columnHeight = inColumn * TILE.height + (inColumn - 1) * rowGap;
      positions[sub.id] = {
        x: x + column * (TILE.width + subGap),
        y: headerHeight + (bodyHeight - columnHeight) / 2 + (i % rowLimit) * (TILE.height + rowGap),
      };
    });
    const width = subColumns * TILE.width + (subColumns - 1) * subGap;
    columns.push({ wave, x, width, height: headerHeight + bodyHeight, count: list.length });
    x += width + colGap;
  }
  return { positions, columns };
}

export const CHIP = { width: 196, height: 26, gap: 6 };

// groups: [{ id, nodes: [{ id, path, qualified_name }] }]. Returns the territory rectangles and
// the position of every node inside them.
export function packTerritories(groups, { pad = 16, headerHeight = 58, gutter = 44, aspect = 1.7 } = {}) {
  const sized = groups
    .filter((g) => g.nodes.length)
    .map((g) => {
      const n = g.nodes.length;
      // chips are wide and short: this column count makes a territory roughly 1.3x wider than tall
      const cols = Math.max(1, Math.min(8, Math.round(0.5 * Math.sqrt(n))));
      const rows = Math.ceil(n / cols);
      return {
        id: g.id,
        nodes: [...g.nodes].sort((a, b) => (a.path || "").localeCompare(b.path || "") || (a.qualified_name || "").localeCompare(b.qualified_name || "")),
        cols,
        width: pad * 2 + cols * CHIP.width + (cols - 1) * CHIP.gap,
        height: headerHeight + pad + rows * CHIP.height + (rows - 1) * CHIP.gap,
      };
    })
    .sort((a, b) => b.nodes.length - a.nodes.length || String(a.id).localeCompare(String(b.id)));

  const area = sized.reduce((sum, t) => sum + (t.width + gutter) * (t.height + gutter), 0);
  const rowWidth = Math.max(Math.sqrt(area * aspect), sized[0]?.width ?? 0);

  const territories = [];
  const positions = new Map();
  let x = 0;
  let y = 0;
  let rowHeight = 0;
  for (const t of sized) {
    if (x > 0 && x + t.width > rowWidth) {
      x = 0;
      y += rowHeight + gutter;
      rowHeight = 0;
    }
    territories.push({ id: t.id, x, y, width: t.width, height: t.height, count: t.nodes.length });
    t.nodes.forEach((node, i) => {
      positions.set(node.id, {
        x: x + pad + (i % t.cols) * (CHIP.width + CHIP.gap),
        y: y + headerHeight + Math.floor(i / t.cols) * (CHIP.height + CHIP.gap),
      });
    });
    x += t.width + gutter;
    rowHeight = Math.max(rowHeight, t.height);
  }
  return { territories, positions };
}
