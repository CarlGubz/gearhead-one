import { useEffect, useMemo, useRef, useState } from "react";
import { formatNumber } from "../lib/fields";

export interface Series { name: string; color: string; x: number[]; y: number[] }

const PALETTE = ["#2a4bd7", "#d3341d", "#35a870", "#ee8426", "#1b9bc4", "#d9b92e", "#7c5cff", "#596579"]; // Gearhead contour ramp
export const seriesColor = (i: number) => PALETTE[i % PALETTE.length];

function niceTicks(lo: number, hi: number, n = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || !(hi > lo)) return [Number.isFinite(lo) ? lo : 0];
  const span = hi - lo;
  const step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const err = step0 / mag;
  const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
  const out: number[] = [];
  const k0 = Math.ceil(lo / step - 1e-9), k1 = Math.floor(hi / step + 1e-9);
  for (let k = k0; k <= k1 && out.length < 40; k++) out.push(Number((k * step).toPrecision(12)) + 0);
  return out;
}

export function LineChart(props: {
  series: Series[];
  height?: number;
  logY?: boolean;
  xLabel?: string;
  yLabel?: string;
}) {
  const { series, height = 230, logY, xLabel, yLabel } = props;
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(500);
  const [hoverX, setHoverX] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const m = { l: 58, r: 12, t: 8, b: yLabel || xLabel ? 34 : 24 };
  const iw = Math.max(50, w - m.l - m.r), ih = height - m.t - m.b;

  const data = useMemo(() => {
    let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    const tf = (v: number) => (logY ? Math.log10(v) : v);
    const ok = (v: number) => Number.isFinite(v) && (!logY || v > 0);
    for (const s of series)
      for (let i = 0; i < s.x.length; i++) {
        if (!ok(s.y[i])) continue;
        const y = tf(s.y[i]);
        xmin = Math.min(xmin, s.x[i]); xmax = Math.max(xmax, s.x[i]);
        ymin = Math.min(ymin, y); ymax = Math.max(ymax, y);
      }
    if (!Number.isFinite(xmin)) { xmin = 0; xmax = 1; ymin = 0; ymax = 1; }
    if (xmax === xmin) xmax = xmin + 1;
    if (ymax - ymin <= 1e-9 * Math.max(1, Math.abs(ymax), Math.abs(ymin))) { const d = Math.max(1e-9, Math.abs(ymax) * 0.05, logY ? 1 : 1e-6); ymax += d; ymin -= d; }
    const pad = (ymax - ymin) * 0.06;
    if (logY) { ymin = Math.floor(ymin); ymax = Math.ceil(ymax); } else { ymin -= pad; ymax += pad; }
    return { xmin, xmax, ymin, ymax, tf, ok };
  }, [series, logY]);

  const X = (x: number) => m.l + ((x - data.xmin) / (data.xmax - data.xmin)) * iw;
  const Y = (y: number) => m.t + (1 - (data.tf(y) - data.ymin) / (data.ymax - data.ymin)) * ih;
  const xt = niceTicks(data.xmin, data.xmax, Math.max(2, Math.floor(iw / 90)));
  const yt = logY ? Array.from({ length: Math.max(1, data.ymax - data.ymin + 1) }, (_, i) => data.ymin + i) : niceTicks(data.ymin, data.ymax, 4);

  const hover = useMemo(() => {
    if (hoverX === null) return null;
    const rows = series.map((s) => {
      let best = -1, bd = Infinity;
      for (let i = 0; i < s.x.length; i++) { const d = Math.abs(s.x[i] - hoverX); if (d < bd) { bd = d; best = i; } }
      return best >= 0 ? { name: s.name, color: s.color, x: s.x[best], y: s.y[best] } : null;
    }).filter(Boolean) as { name: string; color: string; x: number; y: number }[];
    return rows;
  }, [hoverX, series]);

  return (
    <div ref={box} style={{ position: "relative" }}>
      <svg
        className="chart"
        width={w}
        height={height}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGElement).getBoundingClientRect();
          const px = e.clientX - r.left;
          if (px < m.l || px > m.l + iw) return setHoverX(null);
          setHoverX(data.xmin + ((px - m.l) / iw) * (data.xmax - data.xmin));
        }}
        onMouseLeave={() => setHoverX(null)}
      >
        {yt.map((v, i) => {
          const py = logY ? m.t + (1 - (v - data.ymin) / (data.ymax - data.ymin)) * ih : Y(v);
          return (
            <g key={i}>
              <line className="gridl" x1={m.l} x2={m.l + iw} y1={py} y2={py} />
              <text x={m.l - 6} y={py + 4} textAnchor="end">{logY ? `1e${v}` : formatNumber(v, 3)}</text>
            </g>
          );
        })}
        {xt.map((v, i) => (
          <g key={i}>
            <line className="gridl" x1={X(v)} x2={X(v)} y1={m.t} y2={m.t + ih} />
            <text x={X(v)} y={m.t + ih + 14} textAnchor="middle">{formatNumber(v, 3)}</text>
          </g>
        ))}
        <line className="axis" x1={m.l} x2={m.l + iw} y1={m.t + ih} y2={m.t + ih} />
        <line className="axis" x1={m.l} x2={m.l} y1={m.t} y2={m.t + ih} />
        {series.map((s) => {
          let d = "";
          let pen = false;
          for (let i = 0; i < s.x.length; i++) {
            if (!data.ok(s.y[i])) { pen = false; continue; }
            d += `${pen ? "L" : "M"}${X(s.x[i]).toFixed(1)},${Y(s.y[i]).toFixed(1)}`;
            pen = true;
          }
          return <path key={s.name} d={d} fill="none" stroke={s.color} strokeWidth={1.7} strokeLinejoin="round" />;
        })}
        {hoverX !== null && <line x1={X(hoverX)} x2={X(hoverX)} y1={m.t} y2={m.t + ih} stroke="var(--muted)" strokeDasharray="3 3" />}
        {xLabel && <text x={m.l + iw / 2} y={height - 4} textAnchor="middle">{xLabel}</text>}
        {yLabel && <text transform={`translate(11 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>}
      </svg>
      <div className="row wrap small" style={{ gap: 14, padding: "2px 4px 0 58px" }}>
        {series.map((s) => (
          <span key={s.name} className="row" style={{ gap: 5 }}>
            <i style={{ width: 14, height: 3, background: s.color, display: "inline-block", borderRadius: 2 }} />
            <span className="muted">{s.name}</span>
          </span>
        ))}
      </div>
      {hover && hover.length > 0 && (
        <div className="readout" style={{ left: Math.min(w - 190, X(hover[0].x) + 12), top: 6 }}>
          <div>{xLabel ?? "x"} = {formatNumber(hover[0].x, 5)}</div>
          {hover.map((r) => (
            <div key={r.name}><span style={{ color: r.color }}>■</span> {r.name}: {formatNumber(r.y, 4)}</div>
          ))}
        </div>
      )}
    </div>
  );
}
