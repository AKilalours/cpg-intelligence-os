// Server-rendered SVG: no chart library, no client JS. Units are parsed to
// numbers only for drawing; displayed figures stay exact strings.
export function Sparkline({ values, width = 640, height = 120 }: { values: string[]; width?: number; height?: number }) {
  const nums = values.map(Number);
  if (nums.length < 2) return null;
  const max = Math.max(...nums, 1);
  const step = width / (nums.length - 1);
  const points = nums.map((v, i) => `${(i * step).toFixed(1)},${(height - (v / max) * (height - 4) - 2).toFixed(1)}`);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-32 w-full" role="img"
         aria-label={`Daily units, ${nums.length} days, peak ${max}`}>
      <line x1="0" y1={height - 1} x2={width} y2={height - 1} stroke="#C9D8C6" strokeWidth="2" />
      <polyline points={points.join(" ")} fill="none" stroke="#1C2B3A" strokeWidth="1.5" />
    </svg>
  );
}
