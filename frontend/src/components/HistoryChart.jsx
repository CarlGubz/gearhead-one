import React, { useEffect, useMemo, useState } from "react";
import { fmt, minMax } from "./utils.js";

/** Minimal SVG line chart for time histories (transient heat / dynamics). */
export default function HistoryChart({ times, series }) {
  const names = Object.keys(series || {});
  const [name, setName] = useState(names[0]);
  useEffect(() => {
    if (!names.includes(name)) setName(names[0]);
  }, [names, name]);
  const y = series?.[name];
  const geom = useMemo(() => {
    if (!y || !times || y.length !== times.length || !times.length) return null;
    const [t0, t1] = minMax(times);
    let [lo, hi] = minMax(y);
    if (hi - lo < 1e-30) { hi = lo + 1; }
    const W = 240, H = 110, P = 6;
    const pts = times.map((t, i) => `${P + ((t - t0) / (t1 - t0 || 1)) * (W - 2 * P)},${H - P - ((y[i] - lo) / (hi - lo)) * (H - 2 * P)}`);
    return { pts: pts.join(" "), t0, t1, lo, hi, W, H };
  }, [y, times]);
  if (!names.length || !geom) return null;
  return (
    <>
      <h4>History</h4>
      <select value={name} onChange={(e) => setName(e.target.value)}>
        {names.map((n) => <option key={n} value={n}>{n.replace(/_/g, " ")}</option>)}
      </select>
      <svg viewBox={`0 0 ${geom.W} ${geom.H}`} className="history-chart" role="img" aria-label={`History of ${name}`}>
        <rect x="0.5" y="0.5" width={geom.W - 1} height={geom.H - 1} fill="none" stroke="var(--ws-border-strong)" />
        <polyline points={geom.pts} fill="none" stroke="var(--ws-accent)" strokeWidth="1.6" />
      </svg>
      <div className="chart-axis">
        <span>{fmt(geom.lo)}</span><span>t: {fmt(geom.t0)} → {fmt(geom.t1)} s</span><span>{fmt(geom.hi)}</span>
      </div>
    </>
  );
}
