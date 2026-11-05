"use client";

import { useEffect, useState } from "react";

// Counts up once on load (the page's one moment of motion); shows the final value at once with reduced motion.
export function CountUp({ to, format }: { to: number; format: "int" | "money" | "pct" }) {
  const [v, setV] = useState(to);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || to === 0) return;
    const t0 = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / 900);
      setV(to * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    setV(0);
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to]);
  const text = format === "money"
    ? v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 })
    : format === "pct" ? `${v.toFixed(v === to ? 2 : 1)}%` : Math.round(v).toLocaleString("en-US");
  return <span className="tabular-nums">{text}</span>;
}
