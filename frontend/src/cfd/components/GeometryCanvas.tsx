import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CaseConfig, Side } from "../types";
import { SIDES } from "../types";
import { footprint, insideObstacle, meshSection, rasterize, tidy } from "../lib/geometry";
import { cssVar, useThemeTick } from "../lib/theme";

export type Selection = { kind: "obstacle" | "heat" | "probe"; id: string } | null;

const SIDE_COLOR: Record<string, string> = { wall: "#6b7785", inlet: "#1d7fd6", outlet: "#1f9d62", symmetry: "#8b5cf6" };
const MARGIN = 56;

interface Props {
  config: CaseConfig;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onMove: (sel: NonNullable<Selection>, x: number, y: number) => void;
  showGrid: boolean;
}

export function GeometryCanvas({ config, selection, onSelect, onMove, showGrid }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 600, h: 400 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ sel: NonNullable<Selection>; dx: number; dy: number } | null>(null);
  const themeTick = useThemeTick();
  const mask = useMemo(() => rasterize(config), [config.domain, config.mesh, config.obstacles]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const view = useMemo(() => {
    const { width: lx, height: ly } = config.domain;
    const s = Math.min((size.w - 2 * MARGIN) / lx, (size.h - 2 * MARGIN) / ly);
    const ox = (size.w - lx * s) / 2, oy = (size.h - ly * s) / 2;
    return { s: Math.max(s, 1), ox, oy, lx, ly };
  }, [size, config.domain]);

  const toPx = useCallback((x: number, y: number): [number, number] => [view.ox + x * view.s, view.oy + (view.ly - y) * view.s], [view]);
  const toWorld = useCallback((px: number, py: number): [number, number] => [(px - view.ox) / view.s, view.ly - (py - view.oy) / view.s], [view]);

  const hit = useCallback(
    (x: number, y: number): Selection => {
      const tol = 7 / view.s;
      for (const p of [...config.probes].reverse()) if (Math.hypot(p.x - x, p.y - y) < tol * 1.5) return { kind: "probe", id: p.id };
      for (const h of [...config.heat_sources].reverse())
        if (Math.abs(x - h.x) <= h.width / 2 && Math.abs(y - h.y) <= h.height / 2) return { kind: "heat", id: h.id };
      for (const o of [...config.obstacles].reverse()) {
        if (o.shape === "mesh" ? insideObstacle(o, x, y) || (Math.abs(x - o.x) <= tol * 2 && Math.abs(y - o.y) <= tol * 2)
          : o.shape === "rectangle" ? Math.abs(x - o.x) <= o.width / 2 && Math.abs(y - o.y) <= o.height / 2 : Math.hypot(x - o.x, y - o.y) <= o.radius)
          return { kind: "obstacle", id: o.id };
      }
      return null;
    },
    [config, view.s],
  );

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = size.w * dpr;
    cv.height = size.h * dpr;
    const g = cv.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    const { nx, ny } = config.mesh;
    const [x0, y0] = toPx(0, view.ly);
    const W = view.lx * view.s, H = view.ly * view.s;
    const cw = W / nx, ch = H / ny;

    g.fillStyle = cssVar("--canvas-bg");
    g.fillRect(x0, y0, W, H);

    // solid cells
    g.fillStyle = cssVar("--solid");
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) if (mask[j * nx + i]) g.fillRect(x0 + i * cw, y0 + (ny - 1 - j) * ch, cw + 0.6, ch + 0.6);

    // grid
    if (showGrid && cw >= 3 && ch >= 3) {
      g.strokeStyle = cssVar("--canvas-grid");
      g.lineWidth = 0.5;
      g.beginPath();
      for (let i = 0; i <= nx; i++) { g.moveTo(x0 + i * cw, y0); g.lineTo(x0 + i * cw, y0 + H); }
      for (let j = 0; j <= ny; j++) { g.moveTo(x0, y0 + j * ch); g.lineTo(x0 + W, y0 + j * ch); }
      g.stroke();
    }

    // heat sources
    for (const h of config.heat_sources) {
      const [px, py] = toPx(h.x - h.width / 2, h.y + h.height / 2);
      g.fillStyle = "rgba(240,120,30,0.28)";
      g.fillRect(px, py, h.width * view.s, h.height * view.s);
      g.strokeStyle = "#f07a1e";
      g.setLineDash([5, 3]);
      g.lineWidth = selection?.kind === "heat" && selection.id === h.id ? 2.5 : 1.3;
      g.strokeRect(px, py, h.width * view.s, h.height * view.s);
      g.setLineDash([]);
    }

    // obstacle outlines (true geometry)
    for (const o of config.obstacles) {
      const sel = selection?.kind === "obstacle" && selection.id === o.id;
      g.strokeStyle = o.thermal === "fixed" ? "#e0452f" : cssVar("--text");
      g.lineWidth = sel ? 2.8 : 1.4;
      g.beginPath();
      if (o.shape === "mesh") {
        // imported solid: its section through the centre
        for (const [x1, y1, x2, y2] of meshSection(o)?.segments ?? []) {
          const [ax, ay] = toPx(o.x + x1, o.y + y1);
          const [bx, by] = toPx(o.x + x2, o.y + y2);
          g.moveTo(ax, ay);
          g.lineTo(bx, by);
        }
      } else if (o.shape === "rectangle") {
        const [px, py] = toPx(o.x - o.width / 2, o.y + o.height / 2);
        g.rect(px, py, o.width * view.s, o.height * view.s);
      } else {
        const [px, py] = toPx(o.x, o.y);
        g.arc(px, py, o.radius * view.s, 0, Math.PI * 2);
      }
      g.stroke();
      const [lx, ly] = toPx(o.x, o.y);
      g.font = "600 11px system-ui";
      g.textAlign = "center";
      g.textBaseline = "middle";
      const tw = g.measureText(o.name).width + 8;
      const [hw, hh] = footprint(o);
      const pw = 2 * hw * view.s;
      const ph = 2 * hh * view.s;
      if (pw >= tw && ph >= 16) {
        g.fillStyle = "#fff";
        g.shadowColor = "rgba(0,0,0,0.6)";
        g.shadowBlur = 3;
        g.fillText(o.name, lx, ly);
        g.shadowBlur = 0;
      } else {
        g.fillStyle = cssVar("--text");
        g.fillText(o.name, lx, ly - ph / 2 - 9);
      }
    }

    // domain frame + boundary segments
    const segs: Partial<Record<Side, [number, number, number, number]>> = {
      left: [x0, y0, x0, y0 + H], right: [x0 + W, y0, x0 + W, y0 + H], top: [x0, y0, x0 + W, y0], bottom: [x0, y0 + H, x0 + W, y0 + H],
    };
    g.textAlign = "center";
    for (const side of SIDES) {
      const b = config.boundaries[side];
      const [ax, ay, bx, by] = segs[side]!;
      g.strokeStyle = SIDE_COLOR[b.type];
      g.lineWidth = 4;
      g.setLineDash(b.type === "symmetry" ? [8, 5] : []);
      g.beginPath();
      g.moveTo(ax, ay);
      g.lineTo(bx, by);
      g.stroke();
      g.setLineDash([]);
      // arrows for inlet / outlet / moving wall
      const horizontal = side === "top" || side === "bottom";
      const nArrows = Math.max(3, Math.min(9, Math.round((horizontal ? W : H) / 70)));
      const out = side === "left" ? [-1, 0] : side === "right" ? [1, 0] : side === "top" ? [0, -1] : [0, 1];
      let dir: [number, number] | null = null;
      if (b.type === "inlet") dir = [-out[0], -out[1]];
      else if (b.type === "outlet") dir = [out[0], out[1]];
      else if (b.type === "wall" && b.wall_velocity !== 0) {
        const sgn = Math.sign(b.wall_velocity);
        dir = horizontal ? [sgn, 0] : [0, -sgn];
      }
      if (dir) {
        g.strokeStyle = g.fillStyle = b.type === "wall" ? "#c2410c" : SIDE_COLOR[b.type];
        g.lineWidth = 1.8;
        for (let k = 0; k < nArrows; k++) {
          const f = (k + 0.5) / nArrows;
          const cx = ax + (bx - ax) * f, cy = ay + (by - ay) * f;
          const len = 22;
          const off = b.type === "wall" ? 0 : b.type === "inlet" ? -len - 6 : 6;
          const sx = cx + out[0] * off * (b.type === "wall" ? 0 : 1) - (b.type === "wall" ? (dir[0] * len) / 2 : 0);
          const sy = cy + out[1] * off * (b.type === "wall" ? 0 : 1) - (b.type === "wall" ? (dir[1] * len) / 2 : 0);
          const ex = sx + dir[0] * len, ey = sy + dir[1] * len;
          g.beginPath(); g.moveTo(sx, sy); g.lineTo(ex, ey); g.stroke();
          g.beginPath();
          g.moveTo(ex, ey);
          g.lineTo(ex - dir[0] * 7 + dir[1] * 4, ey - dir[1] * 7 + dir[0] * 4);
          g.lineTo(ex - dir[0] * 7 - dir[1] * 4, ey - dir[1] * 7 - dir[0] * 4);
          g.closePath(); g.fill();
        }
      }
      // label
      g.fillStyle = SIDE_COLOR[b.type];
      g.font = "600 11px system-ui";
      g.textBaseline = "middle";
      const text = b.type === "inlet" ? `${side} · inlet ${b.velocity} m/s` : `${side} · ${b.type}`;
      const mx = (ax + bx) / 2, my = (ay + by) / 2;
      const gap = b.type === "inlet" || b.type === "outlet" ? 40 : 15;
      g.save();
      if (side === "left") { g.translate(mx - gap, my); g.rotate(-Math.PI / 2); g.fillText(text, 0, 0); }
      else if (side === "right") { g.translate(mx + gap, my); g.rotate(Math.PI / 2); g.fillText(text, 0, 0); }
      else g.fillText(text, mx, my + (side === "top" ? -gap : gap));
      g.restore();
    }

    // probes
    for (const p of config.probes) {
      const [px, py] = toPx(p.x, p.y);
      const sel = selection?.kind === "probe" && selection.id === p.id;
      g.strokeStyle = "#d946ef";
      g.lineWidth = sel ? 2.6 : 1.6;
      g.beginPath();
      g.arc(px, py, 5, 0, Math.PI * 2);
      g.moveTo(px - 9, py); g.lineTo(px + 9, py); g.moveTo(px, py - 9); g.lineTo(px, py + 9);
      g.stroke();
      g.fillStyle = "#d946ef";
      g.font = "600 11px system-ui";
      g.textAlign = "left";
      g.fillText(p.name, px + 10, py - 9);
    }

    // scale bar
    g.fillStyle = cssVar("--muted");
    g.font = "11px ui-monospace, monospace";
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    g.fillText(`${view.lx} m × ${view.ly} m`, x0, y0 - 28);
  }, [config, size, view, toPx, mask, showGrid, selection, themeTick]);

  const pos = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return toWorld(e.clientX - r.left, e.clientY - r.top);
  };

  return (
    <div className="canvaswrap" ref={wrap}>
      <canvas
        ref={ref}
        style={{ cursor: drag.current ? "grabbing" : hover ? "grab" : "default", touchAction: "none" }}
        onPointerDown={(e) => {
          const [x, y] = pos(e);
          const h = hit(x, y);
          onSelect(h);
          if (h) {
            const item = h.kind === "obstacle" ? config.obstacles.find((o) => o.id === h.id)! : h.kind === "heat" ? config.heat_sources.find((o) => o.id === h.id)! : config.probes.find((o) => o.id === h.id)!;
            drag.current = { sel: h, dx: item.x - x, dy: item.y - y };
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
          }
        }}
        onPointerMove={(e) => {
          const [x, y] = pos(e);
          if (drag.current) {
            onMove(drag.current.sel, tidy(x + drag.current.dx), tidy(y + drag.current.dy));
          } else {
            const h = hit(x, y);
            setHover(h ? `${h.kind}:${h.id}` : null);
          }
        }}
        onPointerUp={() => (drag.current = null)}
      />
    </div>
  );
}
