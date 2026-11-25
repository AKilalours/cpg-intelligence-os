// Picking one point among thousands that overlap: the single nearest point within a radius, plus how many
// others sit on the same spot. Coordinates are in the chart's own units.
export function nearestPoint(pts: [number, number, number][], px: number, py: number, radius = 16) {
  let best = -1, bd = radius * radius;
  for (let k = 0; k < pts.length; k++) { const dx = pts[k][1] - px, dy = pts[k][2] - py, d2 = dx * dx + dy * dy; if (d2 < bd) { bd = d2; best = k; } }
  if (best < 0) return null;
  const [, bx, by] = pts[best];
  let stacked = 0;
  for (const p of pts) if (Math.abs(p[1] - bx) < 3 && Math.abs(p[2] - by) < 3) stacked++;
  return { i: pts[best][0], x: bx, y: by, stacked: stacked - 1 };
}

