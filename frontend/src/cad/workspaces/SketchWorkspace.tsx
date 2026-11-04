import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, url } from "../api";
import { Field, NumField, Section, Select, VecField, fmt } from "../components/ui";
import { useApp } from "../store";
import type { Constraint, Entity, Json, Layer, Sketch } from "../types";

type Pt = [number, number];
type Tool = "select" | "line" | "rect" | "circle" | "arc" | "polygon" | "slot" | "ellipse" | "polyline" | "spline" | "point"
  | "trim" | "extend" | "fillet";
const MODIFY: { id: Tool; label: string; hint: string }[] = [
  { id: "trim", label: "Trim", hint: "Click the piece of a line / arc / circle to cut away between intersections." },
  { id: "extend", label: "Extend", hint: "Click near the end of a line or arc to extend it to the next boundary." },
  { id: "fillet", label: "Fillet", hint: "Click a corner where two lines meet (rect / polyline corners work too)." },
];
const ORTHO_DEG = 3; // line tool snaps to horizontal / vertical within this angle
const DEFAULT_LAYERS: Layer[] = [{ name: "0", color: null, visible: true, locked: false }];
const TOOLS: { id: Tool; label: string; hint: string }[] = [
  { id: "select", label: "Select", hint: "Click entities (shift = multi). Delete removes." },
  { id: "line", label: "Line", hint: "Click start, then end. Lines chain; Esc stops." },
  { id: "rect", label: "Rect", hint: "Click two opposite corners." },
  { id: "circle", label: "Circle", hint: "Click centre, then a point on the circle." },
  { id: "arc", label: "Arc", hint: "Click centre, start point, then end point (counter-clockwise)." },
  { id: "polygon", label: "Polygon", hint: "Click centre, then a vertex." },
  { id: "slot", label: "Slot", hint: "Click both slot centres." },
  { id: "ellipse", label: "Ellipse", hint: "Click centre, then the corner of its bounding box." },
  { id: "polyline", label: "Polyline", hint: "Click points; double-click or Enter to finish. Hold C to close." },
  { id: "spline", label: "Spline", hint: "Click points; double-click or Enter to finish." },
  { id: "point", label: "Point", hint: "Click to place a point." },
];

const rad = (d: number) => (d * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;

/** Sample an entity as polylines in sketch coordinates (display only; the server is authoritative). */
function sample(e: Entity): Pt[][] {
  const arc = (c: Pt, r: number, a0: number, a1: number): Pt[] => {
    let sweep = a1 - a0;
    while (sweep <= 0) sweep += 360;
    const n = Math.max(8, Math.ceil(sweep / 4));
    return Array.from({ length: n + 1 }, (_, i) => [c[0] + r * Math.cos(rad(a0 + (sweep * i) / n)), c[1] + r * Math.sin(rad(a0 + (sweep * i) / n))] as Pt);
  };
  switch (e.type) {
    case "line": return [[e.p1, e.p2]];
    case "rect": { const [a, b] = [e.p1 as Pt, e.p2 as Pt]; return [[a, [b[0], a[1]], b, [a[0], b[1]], a]]; }
    case "circle": return [arc(e.c, e.r, 0, 360)];
    case "arc": return [arc(e.c, e.r, e.start, e.end)];
    case "polyline": return [e.closed ? [...e.points, e.points[0]] : e.points];
    case "spline": return [e.closed ? [...e.points, e.points[0]] : e.points];
    case "polygon": {
      const pts = Array.from({ length: e.sides }, (_, i) => [e.c[0] + e.r * Math.cos(rad(e.rotation ?? 0) + (2 * Math.PI * i) / e.sides), e.c[1] + e.r * Math.sin(rad(e.rotation ?? 0) + (2 * Math.PI * i) / e.sides)] as Pt);
      return [[...pts, pts[0]]];
    }
    case "slot": {
      const [p1, p2] = [e.p1 as Pt, e.p2 as Pt];
      const a = Math.atan2(p2[1] - p1[1], p2[0] - p1[0]);
      const r = e.width / 2;
      const cap = (c: Pt, from: number) => Array.from({ length: 13 }, (_, i) => [c[0] + r * Math.cos(from + (Math.PI * i) / 12), c[1] + r * Math.sin(from + (Math.PI * i) / 12)] as Pt);
      const out = [...cap(p2, a - Math.PI / 2), ...cap(p1, a + Math.PI / 2)];
      return [[...out, out[0]]];
    }
    case "ellipse": {
      const th = rad(e.rotation ?? 0);
      return [Array.from({ length: 65 }, (_, i) => {
        const t = (2 * Math.PI * i) / 64;
        const x = e.rx * Math.cos(t), y = e.ry * Math.sin(t);
        return [e.c[0] + x * Math.cos(th) - y * Math.sin(th), e.c[1] + x * Math.sin(th) + y * Math.cos(th)] as Pt;
      })];
    }
    default: return [];
  }
}

function snapPoints(entities: Entity[]): Pt[] {
  const out: Pt[] = [];
  for (const e of entities) {
    if (e.p1) out.push(e.p1);
    if (e.p2) out.push(e.p2);
    if (e.c) out.push(e.c);
    if (e.p) out.push(e.p);
    if (e.type === "rect") { out.push([e.p1[0], e.p2[1]], [e.p2[0], e.p1[1]]); }
    if (e.type === "arc") { out.push([e.c[0] + e.r * Math.cos(rad(e.start)), e.c[1] + e.r * Math.sin(rad(e.start))], [e.c[0] + e.r * Math.cos(rad(e.end)), e.c[1] + e.r * Math.sin(rad(e.end))]); }
    if (e.points && e.type !== "spline") out.push(...e.points);
  }
  return out;
}

function pointRefs(entities: Entity[]): string[] {
  const r: string[] = [];
  for (const e of entities) {
    r.push(e.id);
    if (e.type === "line" || e.type === "rect" || e.type === "slot") r.push(`${e.id}.p1`, `${e.id}.p2`);
    if (e.type === "line") r.push(`${e.id}.mid`);
    if (e.type === "circle" || e.type === "arc" || e.type === "polygon" || e.type === "ellipse") r.push(`${e.id}.c`);
    if (e.type === "arc") r.push(`${e.id}.start`, `${e.id}.end`);
    if (e.type === "point") r.push(`${e.id}.p`);
    if (e.type === "polyline") e.points.forEach((_: Pt, i: number) => r.push(`${e.id}.p${i}`));
  }
  return r;
}

const STATUS_PILL: Record<string, string> = { fully_constrained: "ok", solved: "ok", unconstrained: "warn", conflict: "bad", error: "bad", empty: "" };

export default function SketchWorkspace() {
  const { meta, project, sketches, sketchId, setSketchId, upsertSketch, refreshSketches, notify, run } = useApp();
  const stored = sketches.find((s) => s.id === sketchId) ?? null;
  const [draft, setDraft] = useState<Sketch | null>(null);
  const [solve, setSolve] = useState<{ status: string; dof: number; message?: string } | null>(null);
  const [analysis, setAnalysis] = useState<Json>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [sel, setSel] = useState<string[]>([]);
  const [pts, setPts] = useState<Pt[]>([]);
  const [cursor, setCursor] = useState<Pt | null>(null);
  const [view, setView] = useState({ s: 4, ox: 0, oy: 0 });
  const [grid, setGrid] = useState(5);
  const [snap, setSnap] = useState(true);
  const [sides, setSides] = useState(6);
  const [slotW, setSlotW] = useState(6);
  const [construction, setConstruction] = useState(false);
  const [autoCon, setAutoCon] = useState(true);
  const [filletR, setFilletR] = useState(2);
  const [layer, setLayer] = useState("0");
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const saveTimer = useRef<number>();
  const dirty = useRef(false);
  const draftRef = useRef<Sketch | null>(null);
  draftRef.current = draft;
  const importRef = useRef<HTMLInputElement>(null);
  const layers = draft?.layers?.length ? draft.layers : DEFAULT_LAYERS;
  const layerOf = (e: Entity) => layers.find((l) => l.name === (e.layer ?? "0")) ?? layers[0];

  // load the stored sketch into the editable draft when selection changes
  useEffect(() => {
    setDraft(stored ? structuredClone(stored) : null);
    setSel([]); setPts([]); setSolve(null);
    if (stored) fitView(stored.entities);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sketchId]);

  const size = () => {
    const r = svgRef.current?.getBoundingClientRect();
    return { w: r?.width ?? 800, h: r?.height ?? 600 };
  };
  const fitView = (ents: Entity[]) => {
    const all = ents.flatMap((e) => sample(e).flat());
    const { w, h } = size();
    if (!all.length) return setView({ s: 4, ox: w / 2, oy: h / 2 });
    const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const s = Math.min((w * 0.8) / Math.max(x1 - x0, 1), (h * 0.8) / Math.max(y1 - y0, 1), 60);
    setView({ s, ox: w / 2 - ((x0 + x1) / 2) * s, oy: h / 2 + ((y0 + y1) / 2) * s });
  };

  const persist = useCallback((d: Sketch) => {
    window.clearTimeout(saveTimer.current);
    dirty.current = true;
    saveTimer.current = window.setTimeout(async () => {
      dirty.current = false;
      try {
        let saved = await api.updateSketch(d.id, { name: d.name, plane: d.plane, offset: d.offset, entities: d.entities, constraints: d.constraints, layers: d.layers });
        if (d.constraints.length) {
          const r = await api.solveSketch(d.id);
          setSolve({ status: r.status, dof: r.dof, message: r.message });
          if (r.status !== "conflict" && r.status !== "error") { saved = r.sketch; setDraft((cur) => (cur && cur.id === saved.id ? { ...cur, entities: saved.entities } : cur)); }
        } else setSolve(null);
        upsertSketch(saved);
        setAnalysis(await api.analyseSketch(d.id));
      } catch (e) { notify(String(e)); }
    }, 450);
  }, [notify, upsertSketch]);

  const update = (patch: Partial<Sketch>) => {
    if (!draft) return;
    const next = { ...draft, ...patch };
    setDraft(next);
    persist(next);
  };

  /** Save a pending debounced edit now, so a server-side operation starts from the latest geometry. */
  const flush = async () => {
    const d = draftRef.current;
    if (!d || !dirty.current) return;
    window.clearTimeout(saveTimer.current);
    dirty.current = false;
    await api.updateSketch(d.id, { name: d.name, plane: d.plane, offset: d.offset, entities: d.entities, constraints: d.constraints, layers: d.layers });
  };

  /** Apply a server result (edit op / auto-constrain) to the editor. */
  const adopt = async (s: Sketch, status?: { status: string; dof: number } | null) => {
    setDraft(s); upsertSketch(s); setSel([]);
    if (status !== undefined) setSolve(status);
    setAnalysis(await api.analyseSketch(s.id));
  };

  const editOp = async (op: string, params: Json, ids?: string[]) => {
    if (!draft) return;
    const r = await run(`${op}…`, async () => { await flush(); return api.sketchEdit(draft.id, op, params, ids); });
    if (r) await adopt(r, r.solve);
  };

  useEffect(() => { if (stored) api.analyseSketch(stored.id).then(setAnalysis).catch(() => {}); }, [stored?.id]); // eslint-disable-line

  // ------------------------------------------------------------ coordinates
  const toWorld = (cx: number, cy: number): Pt => {
    const r = svgRef.current!.getBoundingClientRect();
    return [(cx - r.left - view.ox) / view.s, -(cy - r.top - view.oy) / view.s];
  };
  const snapped = (p: Pt, free = false): Pt => {
    if (!snap || free) return p;
    const tol = 8 / view.s;
    let best: Pt | null = null, bd = tol;
    for (const q of snapPoints(draft?.entities ?? [])) {
      const d = Math.hypot(q[0] - p[0], q[1] - p[1]);
      if (d < bd) { bd = d; best = q; }
    }
    if (best) return best;
    return [Math.round(p[0] / grid) * grid, Math.round(p[1] / grid) * grid];
  };
  /** Line tool: lock to horizontal / vertical when close, unless the point snapped onto existing geometry. */
  const ortho = (p: Pt, free: boolean): Pt => {
    if (free || !snap || tool !== "line" || !pts.length) return p;
    const prev = pts[pts.length - 1];
    const onGeom = snapPoints(draft?.entities ?? []).some((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < 1e-9);
    if (onGeom) return p;
    const dx = p[0] - prev[0], dy = p[1] - prev[1], t = Math.tan(rad(ORTHO_DEG));
    if (Math.abs(dy) <= t * Math.abs(dx)) return [p[0], prev[1]];
    if (Math.abs(dx) <= t * Math.abs(dy)) return [prev[0], p[1]];
    return p;
  };
  const pick = (ev: { clientX: number; clientY: number; altKey: boolean }): Pt => ortho(snapped(toWorld(ev.clientX, ev.clientY), ev.altKey), ev.altKey);

  const nextId = (): string => {
    const used = new Set(draft?.entities.map((e) => e.id));
    let n = 1;
    while (used.has(`e${n}`)) n++;
    return `e${n}`;
  };
  const addEntity = (e: Omit<Entity, "id">) => {
    if (!draft) return;
    const ent = { id: nextId(), ...e, ...(construction ? { construction: true } : {}), ...(layer !== "0" ? { layer } : {}) } as Entity;
    const next = { ...draft, entities: [...draft.entities, ent] };
    setDraft(next);
    draftRef.current = next;
    persist(next);
    if (!autoCon) return;
    // infer horizontal / vertical / coincident / on-curve / tangent relations for the new entity
    api.inferPreview(next.entities, next.constraints, [ent.id]).then(({ constraints }) => {
      const cur = draftRef.current;
      if (!constraints.length || !cur || cur.id !== next.id || !cur.entities.some((x) => x.id === ent.id)) return;
      const merged = { ...cur, constraints: [...cur.constraints, ...constraints] };
      setDraft(merged);
      draftRef.current = merged;
      persist(merged);
    }).catch(() => { /* inference is best-effort */ });
  };

  const finishPoly = (list: Pt[], closed = false) => {
    if (list.length >= 2) addEntity({ type: tool === "spline" ? "spline" : "polyline", points: list, closed });
    setPts([]);
  };

  const onClick = (ev: React.MouseEvent) => {
    if (!draft || tool === "select" || tool === "trim" || tool === "extend") return;
    if (tool === "fillet") {
      editOp("fillet", { at: snapped(toWorld(ev.clientX, ev.clientY), ev.altKey), radius: filletR, tol: 10 / view.s });
      return;
    }
    const p = pick(ev);
    const all = [...pts, p];
    const [a, b, c] = all;
    const r = (u: Pt, v: Pt) => Math.hypot(u[0] - v[0], u[1] - v[1]);
    switch (tool) {
      case "line":
        if (all.length === 2) { addEntity({ type: "line", p1: a, p2: b }); setPts([b]); } else setPts(all);
        break;
      case "rect": if (all.length === 2) { addEntity({ type: "rect", p1: a, p2: b }); setPts([]); } else setPts(all); break;
      case "circle": if (all.length === 2) { if (r(a, b) > 1e-6) addEntity({ type: "circle", c: a, r: r(a, b) }); setPts([]); } else setPts(all); break;
      case "arc":
        if (all.length === 3) {
          const rr = r(a, b);
          if (rr > 1e-6) addEntity({ type: "arc", c: a, r: rr, start: deg(Math.atan2(b[1] - a[1], b[0] - a[0])), end: deg(Math.atan2(c[1] - a[1], c[0] - a[0])) });
          setPts([]);
        } else setPts(all);
        break;
      case "polygon":
        if (all.length === 2) { if (r(a, b) > 1e-6) addEntity({ type: "polygon", c: a, r: r(a, b), sides, rotation: deg(Math.atan2(b[1] - a[1], b[0] - a[0])) }); setPts([]); } else setPts(all);
        break;
      case "slot": if (all.length === 2) { if (r(a, b) > 1e-6) addEntity({ type: "slot", p1: a, p2: b, width: slotW }); setPts([]); } else setPts(all); break;
      case "ellipse":
        if (all.length === 2) { const rx = Math.abs(b[0] - a[0]), ry = Math.abs(b[1] - a[1]); if (rx > 1e-6 && ry > 1e-6) addEntity({ type: "ellipse", c: a, rx, ry, rotation: 0 }); setPts([]); } else setPts(all);
        break;
      case "point": addEntity({ type: "point", p }); break;
      case "polyline":
      case "spline": setPts(all); break;
    }
  };

  const remove = (ids: string[]) => {
    if (!draft || !ids.length) return;
    const gone = new Set(ids);
    const touches = (c: Constraint) => [c.a, c.b, c.c].some((r) => typeof r === "string" && gone.has(r.split(".")[0]));
    const next = { ...draft, entities: draft.entities.filter((e) => !gone.has(e.id)), constraints: draft.constraints.filter((c) => !touches(c)) };
    setDraft(next); setSel([]); persist(next);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // composedPath()[0]: inside the Gearhead shell this workspace lives in a shadow root, where e.target is the host
      const t = (e.composedPath?.()[0] ?? e.target) as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable) return;
      if (e.key === "Escape") { setPts([]); setTool("select"); }
      else if ((e.key === "Delete" || e.key === "Backspace") && sel.length) { e.preventDefault(); remove(sel); }
      else if (e.key === "Enter" && (tool === "polyline" || tool === "spline")) finishPoly(pts, e.shiftKey);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ------------------------------------------------------------ panning/zooming
  const onWheel = (e: React.WheelEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    setView((v) => ({ s: v.s * f, ox: mx - (mx - v.ox) * f, oy: my - (my - v.oy) * f }));
  };

  const planes = meta?.planes ?? ["XY", "XZ", "YZ"];
  if (!project) return <div className="empty"><div><h3>No project</h3></div></div>;

  const hint = [...TOOLS, ...MODIFY].find((t) => t.id === tool)!.hint;
  const visible = (e: Entity) => layerOf(e).visible;
  const editable = (e: Entity) => visible(e) && !layerOf(e).locked;
  const onEntityClick = (ev: React.MouseEvent, e: Entity) => {
    if (!editable(e)) return;
    if (tool === "select") {
      ev.stopPropagation();
      setSel(ev.shiftKey ? (sel.includes(e.id) ? sel.filter((x) => x !== e.id) : [...sel, e.id]) : [e.id]);
    } else if (tool === "trim" || tool === "extend") {
      ev.stopPropagation();
      editOp(tool, { id: e.id, at: toWorld(ev.clientX, ev.clientY) });
    }
  };
  const setLayers = (ls: Layer[], entities = draft?.entities) => update({ layers: ls, entities });
  const sc = view.s;
  const toS = (p: Pt) => `${p[0] * sc + view.ox},${-p[1] * sc + view.oy}`;
  const pathOf = (poly: Pt[]) => "M" + poly.map(toS).join(" L");

  // preview geometry
  const preview = (() => {
    if (!cursor || !pts.length) return [] as Pt[][];
    const a = pts[0];
    const r = Math.hypot(cursor[0] - a[0], cursor[1] - a[1]);
    switch (tool) {
      case "line": return [[pts[pts.length - 1], cursor]];
      case "rect": return sample({ id: "_", type: "rect", p1: a, p2: cursor });
      case "circle": return sample({ id: "_", type: "circle", c: a, r });
      case "arc": return pts.length === 1 ? [[a, cursor]] : sample({ id: "_", type: "arc", c: a, r: Math.hypot(pts[1][0] - a[0], pts[1][1] - a[1]), start: deg(Math.atan2(pts[1][1] - a[1], pts[1][0] - a[0])), end: deg(Math.atan2(cursor[1] - a[1], cursor[0] - a[0])) });
      case "polygon": return sample({ id: "_", type: "polygon", c: a, r, sides, rotation: deg(Math.atan2(cursor[1] - a[1], cursor[0] - a[0])) });
      case "slot": return sample({ id: "_", type: "slot", p1: a, p2: cursor, width: slotW });
      case "ellipse": return sample({ id: "_", type: "ellipse", c: a, rx: Math.abs(cursor[0] - a[0]), ry: Math.abs(cursor[1] - a[1]), rotation: 0 });
      case "polyline": case "spline": return [[...pts, cursor]];
    }
    return [];
  })();

  // grid lines
  const { w, h } = size();
  const gstep = (() => { let g = grid; while (g * sc < 12) g *= 5; return g; })();
  const gx0 = Math.floor((-view.ox / sc) / gstep) * gstep, gx1 = (w - view.ox) / sc;
  const gy0 = Math.floor(((view.oy - h) / sc) / gstep) * gstep, gy1 = view.oy / sc;
  const gridLines: JSX.Element[] = [];
  for (let x = gx0; x <= gx1; x += gstep) gridLines.push(<line key={"x" + x} x1={x * sc + view.ox} x2={x * sc + view.ox} y1={0} y2={h} style={{ stroke: Math.abs(x) < 1e-9 ? "var(--sk-axis-y)" : "var(--sk-grid)" }} />);
  for (let y = gy0; y <= gy1; y += gstep) gridLines.push(<line key={"y" + y} y1={-y * sc + view.oy} y2={-y * sc + view.oy} x1={0} x2={w} style={{ stroke: Math.abs(y) < 1e-9 ? "var(--sk-axis-x)" : "var(--sk-grid)" }} />);

  const selEntities = draft?.entities.filter((e) => sel.includes(e.id)) ?? [];

  return (
    <div className="workspace">
      {/* ---------------------------------------------------------------- left */}
      <div className="side left">
        <Section title="Sketches" actions={
          <>
            <button className="btn sm" onClick={async () => {
              const s = await run("creating…", () => api.createSketch(project.id, { name: `Sketch ${sketches.length + 1}`, plane: "XY", entities: [], constraints: [] }));
              if (s) { upsertSketch(s); setSketchId(s.id); }
            }}>＋ New</button>
            <button className="btn sm" onClick={() => importRef.current?.click()}>⇪ DXF</button>
            <input ref={importRef} type="file" hidden accept=".dxf" onChange={async (e) => {
              const f = e.target.files?.[0]; e.target.value = "";
              if (!f) return;
              const s = await run("importing…", () => api.importDxf(project.id, f, "XY"));
              if (s) { upsertSketch(s); setSketchId(s.id); }
            }} />
          </>
        } pad={false}>
          <div className="list">
            {sketches.map((s) => (
              <div key={s.id} className={`item ${s.id === sketchId ? "sel" : ""}`} onClick={() => setSketchId(s.id)}>
                <span className="name">{s.name}</span><span className="pill">{s.plane}</span>
                <button className="btn ghost sm" onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete sketch "${s.name}"?`)) return;
                  await run("deleting…", () => api.deleteSketch(s.id));
                  const rest = await refreshSketches();
                  if (s.id === sketchId) setSketchId(rest[0]?.id ?? null);
                }}>✕</button>
              </div>
            ))}
            {!sketches.length && <div className="item muted">No sketches yet</div>}
          </div>
        </Section>

        {draft && (
          <>
            <Section title="Draw">
              <div className="row wrap">
                {TOOLS.map((t) => <button key={t.id} className={`btn sm ${tool === t.id ? "active" : ""}`} onClick={() => { setTool(t.id); setPts([]); }}>{t.label}</button>)}
              </div>
              <div className="row wrap" style={{ marginTop: 6 }}>
                <span className="muted small">Modify</span>
                {MODIFY.map((t) => <button key={t.id} className={`btn sm ${tool === t.id ? "active" : ""}`} onClick={() => { setTool(t.id); setPts([]); }}>{t.label}</button>)}
              </div>
              <div className="muted small" style={{ marginTop: 6 }}>{hint}</div>
              <div className="col" style={{ marginTop: 8 }}>
                {tool === "polygon" && <Field label="Sides"><NumField value={sides} allowExpr={false} onChange={(v) => setSides(Math.max(3, Math.round(v ?? 6)))} /></Field>}
                {tool === "slot" && <Field label="Slot width (mm)"><NumField value={slotW} allowExpr={false} onChange={(v) => setSlotW(v ?? 6)} /></Field>}
                {tool === "fillet" && <Field label="Fillet radius (mm)"><NumField value={filletR} allowExpr={false} onChange={(v) => setFilletR(Math.max(0.01, v ?? 2))} /></Field>}
                <label className="check"><input type="checkbox" checked={autoCon} onChange={(e) => setAutoCon(e.target.checked)} />Auto-constrain while drawing</label>
                <label className="check"><input type="checkbox" checked={construction} onChange={(e) => setConstruction(e.target.checked)} />Construction geometry</label>
                <label className="check"><input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} />Snap to grid / points (Alt = free)</label>
                <Field label="Grid (mm)"><NumField value={grid} allowExpr={false} onChange={(v) => setGrid(Math.max(0.1, v ?? 5))} /></Field>
              </div>
            </Section>
            <LayersPanel layers={layers} current={layer} setCurrent={setLayer} sel={sel} entities={draft.entities} onChange={setLayers} />
            <Section title="Sketch plane">
              <div className="col">
                <Field label="Name"><input type="text" value={draft.name} onChange={(e) => update({ name: e.target.value })} /></Field>
                <div className="row">
                  <div className="grow"><Field label="Plane"><Select value={draft.plane} onChange={(v) => update({ plane: v })} options={planes.map((p) => ({ value: p, label: p }))} /></Field></div>
                  <div className="grow"><Field label="Offset (mm)"><NumField value={draft.offset} allowExpr={false} onChange={(v) => update({ offset: v ?? 0 })} /></Field></div>
                </div>
              </div>
            </Section>
            <Section title="Export">
              <div className="row wrap">
                <a className="btn sm" href={url(`/api/sketches/${draft.id}/export?format=dxf`)} download>DXF</a>
                <a className="btn sm" href={url(`/api/sketches/${draft.id}/export?format=svg`)} download>SVG</a>
              </div>
            </Section>
          </>
        )}
      </div>

      {/* ---------------------------------------------------------------- canvas */}
      <div className="center">
        {draft ? (
          <svg
            ref={svgRef}
            className="sketch-svg"
            style={{ cursor: tool === "select" ? "default" : "crosshair" }}
            onWheel={onWheel}
            onPointerDown={(e) => {
              if (e.button === 1 || e.button === 2 || (e.button === 0 && tool === "select" && (e.target as Element).tagName === "svg")) {
                dragRef.current = { x: e.clientX, y: e.clientY, ox: view.ox, oy: view.oy };
                (e.target as Element).setPointerCapture?.(e.pointerId);
                if (e.button === 0) setSel([]);
              }
            }}
            onPointerMove={(e) => {
              if (dragRef.current) setView((v) => ({ ...v, ox: dragRef.current!.ox + e.clientX - dragRef.current!.x, oy: dragRef.current!.oy + e.clientY - dragRef.current!.y }));
              else setCursor(pick(e));
            }}
            onPointerUp={() => (dragRef.current = null)}
            onClick={onClick}
            onDoubleClick={() => { if (tool === "polyline" || tool === "spline") finishPoly(pts.slice(0, -1)); }}
            onContextMenu={(e) => e.preventDefault()}
          >
            {gridLines}
            {draft.entities.filter(visible).map((e) => {
              const isSel = sel.includes(e.id);
              const lc = layerOf(e).color;
              const locked = !editable(e);
              const pickable = !locked && (tool === "select" || tool === "trim" || tool === "extend");
              if (e.type === "point") {
                return <circle key={e.id} cx={e.p[0] * sc + view.ox} cy={-e.p[1] * sc + view.oy} r={4} style={{ fill: isSel ? "var(--sk-sel)" : lc ?? "var(--sk-point)", opacity: locked ? 0.55 : 1 }} onClick={(ev) => onEntityClick(ev, e)} />;
              }
              const polys = sample(e);
              return (
                <g key={e.id} onClick={(ev) => onEntityClick(ev, e)} style={{ opacity: locked ? 0.55 : 1 }}>
                  {polys.map((p, i) => (
                    <g key={i}>
                      <path d={pathOf(p)} fill="none" stroke="transparent" strokeWidth={12} style={{ cursor: pickable ? (tool === "select" ? "pointer" : "crosshair") : "inherit" }} />
                      <path d={pathOf(p)} fill="none" style={{ stroke: isSel ? "var(--sk-sel)" : e.construction ? "var(--sk-construction)" : lc ?? "var(--sk-line)" }} strokeWidth={isSel ? 2.4 : 1.6} strokeDasharray={e.construction ? "5 4" : undefined} />
                    </g>
                  ))}
                  {isSel && snapPoints([e]).map((q, i) => <rect key={i} x={q[0] * sc + view.ox - 3} y={-q[1] * sc + view.oy - 3} width={6} height={6} style={{ fill: "var(--sk-sel)" }} />)}
                </g>
              );
            })}
            {preview.map((p, i) => <path key={i} d={pathOf(p)} fill="none" style={{ stroke: "var(--sk-sel)" }} strokeWidth={1.4} strokeDasharray="4 3" />)}
            {pts.map((p, i) => <circle key={i} cx={p[0] * sc + view.ox} cy={-p[1] * sc + view.oy} r={3.5} style={{ fill: "var(--sk-sel)" }} />)}
            {cursor && tool !== "select" && <circle cx={cursor[0] * sc + view.ox} cy={-cursor[1] * sc + view.oy} r={5} fill="none" style={{ stroke: "var(--sk-sel)" }} />}
          </svg>
        ) : (
          <div className="empty"><div><h3>No sketch selected</h3>Create a sketch to start drawing, then use it in Extrude / Revolve / Sweep / Loft features.</div></div>
        )}
        {draft && (
          <>
            <div className="overlay tl">
              <div className="hud">
                <b>{draft.name}</b> <span className="pill">{draft.plane}{draft.offset ? ` @ ${draft.offset}` : ""}</span>
                {solve && <> <span className={`pill ${STATUS_PILL[solve.status] ?? ""}`}>{solve.status.replace("_", " ")} · DOF {solve.dof}</span></>}
                <div className="mono small muted">{cursor ? `x ${cursor[0].toFixed(2)}  y ${cursor[1].toFixed(2)}` : " "}</div>
                {solve?.message && <div className="small" style={{ color: "var(--danger)" }}>{solve.message}</div>}
              </div>
            </div>
            <div className="overlay tr">
              <div className="toolbar">
                <button className="btn ghost sm" onClick={() => fitView(draft.entities)}>⤢ fit</button>
                <button className="btn ghost sm" disabled={!sel.length} title="Split rects / polylines / polygons / slots into lines and arcs" onClick={() => editOp("explode", {}, sel)}>explode</button>
                <button className="btn ghost sm" disabled={!sel.length} onClick={() => remove(sel)}>🗑 delete</button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* ---------------------------------------------------------------- right */}
      <div className="side right">
        {draft && (
          <>
            {analysis && (
              <Section title="Profile analysis">
                <div className="kv">
                  <span>Closed loops</span><span>{analysis.closed_loops}</span>
                  <span>Open chains</span><span>{analysis.open_chains}</span>
                  <span>Area</span><span>{fmt(analysis.area)} mm²</span>
                  <span>Perimeter</span><span>{fmt(analysis.perimeter)} mm</span>
                </div>
                <div style={{ marginTop: 6 }}><span className={`pill ${analysis.valid_profile ? "ok" : "warn"}`}>{analysis.valid_profile ? "ready for extrude" : "not a closed profile"}</span></div>
                {(analysis.warnings ?? []).map((w: string, i: number) => <div key={i} className="notice warn" style={{ marginTop: 6 }}>{w}</div>)}
              </Section>
            )}

            {selEntities.length === 1 && <EntityEditor entity={selEntities[0]} onChange={(e) => update({ entities: draft.entities.map((x) => (x.id === e.id ? e : x)) })} />}

            <ConstraintPanel draft={draft} sel={sel} meta={meta} onChange={(constraints) => update({ constraints })} onAuto={async () => {
              const s = await run("auto-constraining…", async () => { await flush(); return api.inferSketch(draft.id); });
              if (!s) return;
              const r = s.constraints.length ? await api.solveSketch(s.id) : null;
              await adopt(r && r.status !== "conflict" && r.status !== "error" ? r.sketch : s, r ? { status: r.status, dof: r.dof } : null);
              notify(`${s.added} constraint${s.added === 1 ? "" : "s"} added`, "ok");
            }} />

            <OpsPanel disabled={!draft} sel={sel} onApply={async (op, params, replace) => {
              const s = await run("applying…", async () => { await flush(); return api.sketchOp(draft.id, op, params, sel.length ? sel : undefined, replace); });
              if (s) { setDraft(s); upsertSketch(s); setSel([]); setAnalysis(await api.analyseSketch(s.id)); }
            }} ops={meta?.sketch_ops ?? []} />
          </>
        )}
      </div>
    </div>
  );
}

function EntityEditor({ entity, onChange }: { entity: Entity; onChange: (e: Entity) => void }) {
  const keys = Object.keys(entity).filter((k) => !["id", "type", "construction", "closed", "points"].includes(k));
  return (
    <Section title={`${entity.type} ${entity.id}`}>
      <div className="col">
        {keys.map((k) => {
          const v = entity[k];
          if (Array.isArray(v)) return <Field key={k} label={k}><VecField n={2} value={v} onChange={(x) => onChange({ ...entity, [k]: x })} /></Field>;
          if (typeof v === "number") return <Field key={k} label={k}><NumField value={v} allowExpr={false} onChange={(x) => onChange({ ...entity, [k]: x ?? 0 })} /></Field>;
          return null;
        })}
        {entity.points && <div className="muted small">{entity.points.length} points</div>}
        <label className="check"><input type="checkbox" checked={!!entity.construction} onChange={(e) => onChange({ ...entity, construction: e.target.checked })} />Construction</label>
        {(entity.type === "polyline" || entity.type === "spline") && (
          <label className="check"><input type="checkbox" checked={!!entity.closed} onChange={(e) => onChange({ ...entity, closed: e.target.checked })} />Closed</label>
        )}
      </div>
    </Section>
  );
}

function LayersPanel({ layers, current, setCurrent, sel, entities, onChange }: {
  layers: Layer[]; current: string; setCurrent: (n: string) => void; sel: string[]; entities: Entity[];
  onChange: (ls: Layer[], entities?: Entity[]) => void;
}) {
  const set = (i: number, patch: Partial<Layer>) => onChange(layers.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const rename = (i: number, name: string) => {
    const old = layers[i].name;
    name = name.trim();
    if (!name || old === "0" || layers.some((l) => l.name === name)) return;
    onChange(layers.map((l, j) => (j === i ? { ...l, name } : l)), entities.map((e) => (e.layer === old ? { ...e, layer: name } : e)));
    if (current === old) setCurrent(name);
  };
  const removeLayer = (i: number) => {
    const old = layers[i].name;
    onChange(layers.filter((_, j) => j !== i), entities.map((e) => (e.layer === old ? (({ layer: _l, ...rest }) => rest)(e) as Entity : e)));
    if (current === old) setCurrent("0");
  };
  const moveSel = (name: string) => onChange(layers, entities.map((e) => {
    if (!sel.includes(e.id)) return e;
    if (name === "0") { const { layer: _l, ...rest } = e; return rest as Entity; }
    return { ...e, layer: name };
  }));
  return (
    <Section title={`Layers (${layers.length})`} actions={
      <button className="btn sm" onClick={() => {
        let n = layers.length; while (layers.some((l) => l.name === `Layer ${n}`)) n++;
        onChange([...layers, { name: `Layer ${n}`, color: null, visible: true, locked: false }]);
      }}>＋ Layer</button>
    }>
      <table className="t">
        <thead><tr><th title="Current layer for new geometry">●</th><th>Name</th><th>Color</th><th title="Visible">👁</th><th title="Locked">🔒</th><th /></tr></thead>
        <tbody>
          {layers.map((l, i) => (
            <tr key={l.name}>
              <td><input type="radio" checked={current === l.name} onChange={() => setCurrent(l.name)} /></td>
              <td>{l.name === "0" ? <span className="mono">0</span> : <input type="text" defaultValue={l.name} onBlur={(e) => rename(i, e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />}</td>
              <td><input type="color" value={l.color ?? "#0b63c5"} title={l.color ?? "theme default"} onChange={(e) => set(i, { color: e.target.value })} style={{ width: 28, height: 22, padding: 0, border: 0, background: "none" }} /></td>
              <td><input type="checkbox" checked={l.visible} onChange={(e) => set(i, { visible: e.target.checked })} /></td>
              <td><input type="checkbox" checked={l.locked} onChange={(e) => set(i, { locked: e.target.checked })} /></td>
              <td>{l.name !== "0" && <button className="btn ghost sm" title="Delete layer (its entities move to layer 0)" onClick={() => removeLayer(i)}>✕</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {sel.length > 0 && (
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted small">Move {sel.length} selected to</span>
          <div className="grow"><Select value="" onChange={(v) => v && moveSel(v)} options={[{ value: "", label: "layer…" }, ...layers.map((l) => ({ value: l.name, label: l.name }))]} /></div>
        </div>
      )}
    </Section>
  );
}

function ConstraintPanel({ draft, sel, meta, onChange, onAuto }: { draft: Sketch; sel: string[]; meta: ReturnType<typeof useApp>["meta"]; onChange: (c: Constraint[]) => void; onAuto: () => void }) {
  const [type, setType] = useState("horizontal");
  const [a, setA] = useState(""), [b, setB] = useState(""), [c, setC] = useState("");
  const [val, setVal] = useState<Json>(10);
  const def = meta?.constraint_types.find((t) => t.type === type);
  const refs = useMemo(() => pointRefs(draft.entities), [draft.entities]);
  const needs = def?.refs ?? [];
  useEffect(() => { setA(sel[0] ?? ""); setB(sel[1] ?? ""); setC(sel[2] ?? ""); }, [sel.join(",")]); // eslint-disable-line
  const add = () => {
    const con: Constraint = { type };
    if (needs.includes("a") && a) con.a = a;
    if (needs.includes("b") && b) con.b = b;
    if (needs.includes("c") && c) con.c = c;
    if (def?.value) con.value = type === "fixed" ? undefined : val;
    onChange([...draft.constraints, con]);
  };
  const ok = needs.every((r) => ({ a, b, c } as Record<string, string>)[r]);
  return (
    <Section title={`Constraints (${draft.constraints.length})`}>
      <div className="col">
        <Select value={type} onChange={setType} options={(meta?.constraint_types ?? []).map((t) => ({ value: t.type, label: t.type.replace(/_/g, " ") }))} />
        {def?.help && <div className="muted small">{def.help}</div>}
        <datalist id="refs">{refs.map((r) => <option key={r} value={r} />)}</datalist>
        <div className="row">
          {needs.includes("a") && <input type="text" list="refs" placeholder="a" value={a} onChange={(e) => setA(e.target.value)} />}
          {needs.includes("b") && <input type="text" list="refs" placeholder="b" value={b} onChange={(e) => setB(e.target.value)} />}
          {needs.includes("c") && <input type="text" list="refs" placeholder="c" value={c} onChange={(e) => setC(e.target.value)} />}
        </div>
        {def?.value && type !== "fixed" && <Field label="Value"><NumField value={val} allowExpr={false} onChange={(v) => setVal(v ?? 0)} /></Field>}
        <div className="row">
          <button className="btn primary sm grow" disabled={!ok} onClick={add}>Add constraint</button>
          <button className="btn sm" title="Detect horizontal / vertical / coincident / on-curve / tangent relations in the whole sketch" onClick={onAuto}>Auto-constrain</button>
        </div>
        <div className="muted small">Tip: select entities on the canvas first to pre-fill references. Point refs look like <span className="mono">e1.p2</span>.</div>
      </div>
      <table className="t" style={{ marginTop: 8 }}>
        <tbody>
          {draft.constraints.map((k, i) => (
            <tr key={i}>
              <td>{k.type.replace(/_/g, " ")}</td>
              <td className="mono">{[k.a, k.b, k.c].filter(Boolean).join(", ")}{k.value !== undefined ? ` = ${JSON.stringify(k.value)}` : ""}</td>
              <td><button className="btn ghost sm" onClick={() => onChange(draft.constraints.filter((_, j) => j !== i))}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

function OpsPanel({ ops, sel, onApply, disabled }: { ops: string[]; sel: string[]; disabled: boolean; onApply: (op: string, params: Json, replace: boolean) => void }) {
  const [op, setOp] = useState("translate");
  const [p, setP] = useState<Record<string, Json>>({ dx: 10, dy: 0, angle: 90, factor: 2, count: 3, distance: 2, center: [0, 0], p1: [0, 0], p2: [0, 10] });
  const set = (k: string, v: Json) => setP((q) => ({ ...q, [k]: v }));
  const fields: Record<string, [string, "n" | "v"][]> = {
    translate: [["dx", "n"], ["dy", "n"]],
    rotate: [["angle", "n"], ["center", "v"]],
    scale: [["factor", "n"], ["center", "v"]],
    mirror: [["p1", "v"], ["p2", "v"]],
    linear_pattern: [["count", "n"], ["dx", "n"], ["dy", "n"]],
    circular_pattern: [["count", "n"], ["angle", "n"], ["center", "v"]],
    offset: [["distance", "n"]],
  };
  return (
    <Section title="Transform / pattern">
      <div className="col">
        <Select value={op} onChange={setOp} options={ops.map((o) => ({ value: o, label: o.replace(/_/g, " ") }))} />
        {(fields[op] ?? []).map(([k, t]) => (
          <Field key={k} label={k}>{t === "n" ? <NumField value={p[k]} allowExpr={false} onChange={(v) => set(k, v ?? 0)} /> : <VecField n={2} value={p[k]} onChange={(v) => set(k, v)} />}</Field>
        ))}
        <div className="muted small">Applies to {sel.length ? `${sel.length} selected` : "all"} entities.</div>
        <div className="row">
          <button className="btn sm" disabled={disabled} onClick={() => onApply(op, p, false)}>Apply (copy)</button>
          {["translate", "rotate", "scale", "mirror"].includes(op) && <button className="btn sm" disabled={disabled} onClick={() => onApply(op, p, true)}>Apply (move)</button>}
        </div>
      </div>
    </Section>
  );
}
