import { useEffect, useMemo, useRef, useState } from "react";
import type { FieldName } from "../types";
import { colormap, gradientCss, type ColormapName } from "../lib/colormap";
import { FIELD_LABELS, formatNumber, streamlines, type DecodedFields } from "../lib/fields";
import { cssVar, useThemeTick } from "../lib/theme";

export interface Overlay {
  obstacles?: { shape: "rectangle" | "circle"; x: number; y: number; width: number; height: number; radius: number }[];
}

interface Props {
  fields: DecodedFields;
  field: FieldName;
  cmap: ColormapName;
  range: [number, number];
  smooth: boolean;
  vectors: boolean;
  streams: boolean;
  line?: { p0: [number, number]; p1: [number, number] } | null;
  probeMode?: boolean;
  onLine?: (p0: [number, number], p1: [number, number]) => void;
  canvasRef?: React.MutableRefObject<HTMLCanvasElement | null>;
  maxHeight?: number;
}

const PAD = 8;

export function FieldCanvas(props: Props) {
  const { fields: f, field, cmap, range, smooth, vectors, streams, line, probeMode, onLine, canvasRef, maxHeight } = props;
  const cv = useRef<HTMLCanvasElement | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  const [hover, setHover] = useState<{ i: number; j: number } | null>(null);
  const [dragLine, setDragLine] = useState<{ p0: [number, number]; p1: [number, number] } | null>(null);
  const themeTick = useThemeTick();

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const base = useMemo(() => {
    const { nx, ny } = f;
    const oc = document.createElement("canvas");
    oc.width = nx;
    oc.height = ny;
    const g = oc.getContext("2d")!;
    const img = g.createImageData(nx, ny);
    const data = f.data[field];
    const [lo, hi] = range;
    const span = hi - lo || 1;
    const [sr, sg, sb] = [91, 103, 115];
    for (let j = 0; j < ny; j++) {
      const row = ny - 1 - j;
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i, o = (row * nx + i) * 4;
        if (f.mask[k]) { img.data[o] = sr; img.data[o + 1] = sg; img.data[o + 2] = sb; img.data[o + 3] = 255; continue; }
        const [r, gg, b] = colormap(cmap, (data[k] - lo) / span);
        img.data[o] = r; img.data[o + 1] = gg; img.data[o + 2] = b; img.data[o + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
    return oc;
  }, [f, field, cmap, range]);

  const lines = useMemo(() => (streams ? streamlines(f) : []), [f, streams]);

  const iw = Math.max(100, w - 2 * PAD);
  const scale = iw / f.lx;
  const ih = Math.min(f.ly * scale, maxHeight ?? 1e9);
  const sc = Math.min(scale, ih / f.ly);
  const dw = f.lx * sc, dh = f.ly * sc;
  const H = dh + 2 * PAD;
  const ox = (w - dw) / 2, oy = PAD;

  useEffect(() => {
    const c = cv.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = w * dpr;
    c.height = H * dpr;
    c.style.height = `${H}px`;
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = cssVar("--canvas-bg");
    g.fillRect(0, 0, w, H);
    g.imageSmoothingEnabled = smooth;
    g.imageSmoothingQuality = "high";
    g.drawImage(base, ox, oy, dw, dh);
    const px = (x: number) => ox + x * sc;
    const py = (y: number) => oy + dh - y * sc;

    if (streams) {
      g.strokeStyle = "rgba(255,255,255,0.7)";
      g.lineWidth = 0.9;
      g.shadowColor = "rgba(0,0,0,0.35)";
      g.shadowBlur = 1;
      for (const L of lines) {
        g.beginPath();
        g.moveTo(px(L[0]), py(L[1]));
        for (let k = 2; k < L.length; k += 2) g.lineTo(px(L[k]), py(L[k + 1]));
        g.stroke();
      }
      g.shadowBlur = 0;
    }
    if (vectors) {
      const dxp = (f.lx / f.nx) * sc;
      const stride = Math.max(1, Math.round(16 / dxp));
      const vmax = Math.max(1e-30, f.ranges.speed[1]);
      g.strokeStyle = g.fillStyle = "rgba(10,20,30,0.85)";
      g.lineWidth = 1;
      for (let j = Math.floor(stride / 2); j < f.ny; j += stride)
        for (let i = Math.floor(stride / 2); i < f.nx; i += stride) {
          const k = j * f.nx + i;
          if (f.mask[k]) continue;
          const u = f.va[k], v = f.vb[k];
          const m = Math.hypot(u, v);
          if (m < vmax * 0.01) continue;
          const len = Math.min(stride * dxp * 1.1, Math.max(3, (m / vmax) * stride * dxp * 1.1));
          const cx = px((i + 0.5) * (f.lx / f.nx)), cy = py((j + 0.5) * (f.ly / f.ny));
          const ux = u / m, uy = -v / m;
          const x1 = cx - (ux * len) / 2, y1 = cy - (uy * len) / 2, x2 = cx + (ux * len) / 2, y2 = cy + (uy * len) / 2;
          g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
          const hs = Math.min(4, len * 0.45);
          g.beginPath();
          g.moveTo(x2, y2);
          g.lineTo(x2 - ux * hs - uy * hs * 0.55, y2 - uy * hs + ux * hs * 0.55);
          g.lineTo(x2 - ux * hs + uy * hs * 0.55, y2 - uy * hs - ux * hs * 0.55);
          g.closePath(); g.fill();
        }
    }
    g.strokeStyle = cssVar("--border");
    g.lineWidth = 1;
    g.strokeRect(ox - 0.5, oy - 0.5, dw + 1, dh + 1);

    const L = dragLine ?? line;
    if (L) {
      g.strokeStyle = "#ff2d95";
      g.lineWidth = 2;
      g.setLineDash([6, 4]);
      g.beginPath(); g.moveTo(px(L.p0[0]), py(L.p0[1])); g.lineTo(px(L.p1[0]), py(L.p1[1])); g.stroke();
      g.setLineDash([]);
      g.fillStyle = "#ff2d95";
      for (const p of [L.p0, L.p1]) { g.beginPath(); g.arc(px(p[0]), py(p[1]), 4, 0, 7); g.fill(); }
    }
  }, [base, lines, vectors, streams, smooth, w, H, ox, oy, dw, dh, sc, line, dragLine, f, themeTick]);

  const toWorld = (e: React.PointerEvent): [number, number] => {
    const r = cv.current!.getBoundingClientRect();
    const x = (e.clientX - r.left - ox) / sc, y = (dh - (e.clientY - r.top - oy)) / sc;
    return [Math.min(Math.max(x, 0), f.lx), Math.min(Math.max(y, 0), f.ly)];
  };

  const set = (el: HTMLCanvasElement | null) => {
    cv.current = el;
    if (canvasRef) canvasRef.current = el;
  };

  const hoverInfo = hover && (() => {
    const k = hover.j * f.nx + hover.i;
    const x = ((hover.i + 0.5) * f.lx) / f.nx, y = ((hover.j + 0.5) * f.ly) / f.ny;
    return { x, y, solid: !!f.mask[k], k };
  })();

  return (
    <div className="fieldwrap" ref={wrap}>
      <canvas
        ref={set}
        style={{ cursor: probeMode ? "crosshair" : "default", touchAction: "none" }}
        onPointerDown={(e) => {
          if (!probeMode) return;
          const p = toWorld(e);
          setDragLine({ p0: p, p1: p });
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const [x, y] = toWorld(e);
          const r = cv.current!.getBoundingClientRect();
          const inside = e.clientX - r.left >= ox && e.clientX - r.left <= ox + dw && e.clientY - r.top >= oy && e.clientY - r.top <= oy + dh;
          setHover(inside ? { i: Math.min(f.nx - 1, Math.floor((x / f.lx) * f.nx)), j: Math.min(f.ny - 1, Math.floor((y / f.ly) * f.ny)) } : null);
          if (dragLine) setDragLine({ p0: dragLine.p0, p1: [x, y] });
        }}
        onPointerUp={() => {
          if (dragLine) {
            const d = Math.hypot(dragLine.p1[0] - dragLine.p0[0], dragLine.p1[1] - dragLine.p0[1]);
            if (d > 0.01 * f.lx) onLine?.(dragLine.p0, dragLine.p1);
            setDragLine(null);
          }
        }}
        onPointerLeave={() => setHover(null)}
      />
      {hoverInfo && (
        <div className="readout">
          <div className="muted">{f.axes[0]} = {formatNumber(hoverInfo.x, 4)} m, {f.axes[1]} = {formatNumber(hoverInfo.y, 4)} m
            {f.plane ? `, ${f.plane.normal} = ${formatNumber(f.plane.position, 4)} m` : ""}</div>
          {hoverInfo.solid ? (
            <div>solid</div>
          ) : (
            (["speed", "u", "v", ...(f.dim === 3 ? ["w"] : []), "p", ...(f.energy ? ["T"] : []), "vorticity"] as FieldName[]).filter((k) => f.data[k]).map((k) => (
              <div key={k} style={{ fontWeight: k === field ? 700 : 400 }}>
                {k.padEnd(9, " ")} {formatNumber(f.data[k][hoverInfo.k], 4)} <span className="muted">{FIELD_LABELS[k].unit}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export function ColorBar({ cmap, range, field }: { cmap: ColormapName; range: [number, number]; field: FieldName }) {
  return (
    <div className="colorbar">
      <span>{formatNumber(range[0], 4)}</span>
      <div className="bar" style={{ background: gradientCss(cmap) }} />
      <span>{formatNumber(range[1], 4)}</span>
      <span>{FIELD_LABELS[field].unit}</span>
    </div>
  );
}
