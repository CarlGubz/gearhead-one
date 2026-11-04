import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, url } from "../api";
import { Field, NumField, Section, Select, fmt } from "../components/ui";
import { useApp } from "../store";
import type { Drawing, DrawingRender, Json, Snap } from "../types";

type Tool = "select" | "horizontal" | "vertical" | "aligned" | "radius" | "diameter" | "note";
const TOOLS: { id: Tool; label: string; hint: string }[] = [
  { id: "select", label: "Select", hint: "Drag a view to move it. Scroll to zoom, middle / right drag to pan." },
  { id: "horizontal", label: "↔ Horiz", hint: "Click two points, then click where the dimension line goes." },
  { id: "vertical", label: "↕ Vert", hint: "Click two points, then click where the dimension line goes." },
  { id: "aligned", label: "⤢ Aligned", hint: "Click two points, then click where the dimension line goes." },
  { id: "radius", label: "R", hint: "Click a circle / arc centre, then click where the leader goes." },
  { id: "diameter", label: "Ø", hint: "Click a hole centre, then click where the leader goes." },
  { id: "note", label: "Note", hint: "Click on the sheet to place a note." },
];
const SCALES = [50, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01];
const scaleLabel = (s: number) => (s >= 1 ? `${s}:1` : `1:${+(1 / s).toFixed(2)}`);
const VIEW_KINDS = ["front", "top", "right", "left", "back", "bottom", "iso"];
const SIZES = ["A4", "A3", "A2", "A1", "A0", "Letter", "Tabloid"];
const TITLE_FIELDS: [string, string][] = [["title", "Title"], ["number", "Drawing no."], ["material", "Material"], ["drawn_by", "Drawn by"],
  ["date", "Date"], ["company", "Company"], ["revision", "Revision"], ["tolerance", "General tolerance"]];
type P2 = { x: number; y: number };

export default function DrawingWorkspace() {
  const { project, parts, partId, notify, run } = useApp();
  const [list, setList] = useState<Drawing[]>([]);
  const [did, setDid] = useState<number | null>(null);
  const [dwg, setDwg] = useState<Drawing | null>(null);
  const [rend, setRend] = useState<DrawingRender | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [pick, setPick] = useState<{ view: string; a?: Snap; b?: Snap } | null>(null);
  const [cursor, setCursor] = useState<P2 | null>(null);
  const [cam, setCam] = useState({ k: 1, tx: 0, ty: 0 });
  const [selView, setSelView] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ id: string; from: P2; ox: number; oy: number; dx: number; dy: number } | null>(null);
  const [pan, setPan] = useState<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const [newPart, setNewPart] = useState<number | null>(partId);
  const [newKind, setNewKind] = useState("front");
  const svgRef = useRef<SVGSVGElement>(null);

  const loadList = useCallback(async () => {
    if (!project) return [];
    const l = await api.drawings(project.id);
    setList(l);
    return l;
  }, [project]);
  useEffect(() => { loadList().then((l) => setDid((cur) => (cur && l.some((d) => d.id === cur) ? cur : l[0]?.id ?? null))).catch((e) => notify(String(e))); }, [loadList, notify]);
  useEffect(() => { if (newPart == null && parts.length) setNewPart(partId ?? parts[0].id); }, [parts, partId, newPart]);

  const refresh = useCallback(async (d?: Drawing) => {
    if (did == null) { setDwg(null); setRend(null); return; }
    try {
      const cur = d ?? list.find((x) => x.id === did) ?? null;
      if (cur) setDwg(cur);
      setRend(await api.renderDrawing(did));
    } catch (e) { notify(String(e)); }
  }, [did, list, notify]);
  useEffect(() => { setPick(null); setSelView(null); setCam({ k: 1, tx: 0, ty: 0 }); refresh(); }, [did]); // eslint-disable-line

  const apply = async (fn: () => Promise<Drawing>, label = "updating…") => {
    const d = await run(label, fn);
    if (d) { setDwg(d); setList((l) => l.map((x) => (x.id === d.id ? d : x))); await refresh(d); }
    return d;
  };
  const patch = (p: Partial<Drawing>) => dwg && apply(() => api.updateDrawing(dwg.id, p));

  // ----------------------------------------------------------- coordinates
  const W = rend?.sheet.w ?? 420, H = rend?.sheet.h ?? 297;
  const toSheet = (cx: number, cy: number): P2 => {
    const svg = svgRef.current!;
    const pt = svg.createSVGPoint();
    pt.x = cx; pt.y = cy;
    const u = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    return { x: (u.x - cam.tx) / cam.k, y: H - (u.y - cam.ty) / cam.k };
  };
  const snaps = useMemo(() => (rend?.views ?? []).flatMap((v) => (v.snaps ?? []).map((s) => ({ ...s, view: v.id }))), [rend]);
  const nearestSnap = (p: P2, onlyCircles: boolean, view?: string) => {
    const tol = 4 / cam.k;
    let best: (Snap & { view: string }) | null = null, bd = tol;
    for (const s of snaps) {
      if (onlyCircles !== (s.end === "c") && onlyCircles) continue;
      if (view && s.view !== view) continue;
      const d = Math.hypot(s.sx - p.x, s.sy - p.y);
      if (d < bd) { bd = d; best = s; }
    }
    return best;
  };

  const offsetFor = (t: Tool, A: Snap, B: Snap, c: P2) => {
    if (t === "horizontal") return c.y - A.sy;
    if (t === "vertical") return A.sx - c.x;
    const L = Math.hypot(B.sx - A.sx, B.sy - A.sy) || 1;
    const n = { x: -(B.sy - A.sy) / L, y: (B.sx - A.sx) / L };
    return (c.x - A.sx) * n.x + (c.y - A.sy) * n.y;
  };

  const onClick = async (ev: React.MouseEvent) => {
    if (!dwg || !rend || tool === "select") return;
    const c = toSheet(ev.clientX, ev.clientY);
    if (tool === "note") {
      const text = prompt("Note text (use \\n for new lines)");
      if (text) patch({ notes: [...dwg.notes, { x: +c.x.toFixed(2), y: +c.y.toFixed(2), text: text.replace(/\\n/g, "\n") }] });
      return;
    }
    const radial = tool === "radius" || tool === "diameter";
    if (!pick?.a) {
      const s = nearestSnap(c, radial);
      if (!s) return notify(radial ? "Click a circle centre (shown as a ring)" : "Click a vertex or hole centre");
      setPick({ view: s.view, a: s });
      return;
    }
    if (radial) {
      const ang = (Math.atan2(c.y - pick.a.sy, c.x - pick.a.sx) * 180) / Math.PI;
      await apply(() => api.addDimension(dwg.id, { view: pick.view, type: tool, a: pick.a, angle: +ang.toFixed(1) }), "adding dimension…");
      setPick(null);
      return;
    }
    if (!pick.b) {
      const s = nearestSnap(c, false, pick.view);
      if (!s) return notify("Pick the second point in the same view");
      if (s.edge === pick.a.edge && s.end === pick.a.end) return;
      setPick({ ...pick, b: s });
      return;
    }
    const offset = offsetFor(tool, pick.a, pick.b, c);
    await apply(() => api.addDimension(dwg.id, { view: pick.view, type: tool, a: pick.a, b: pick.b, offset: +offset.toFixed(2) }), "adding dimension…");
    setPick(null);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setPick(null); setTool("select"); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!project) return <div className="empty"><div><h3>No project</h3></div></div>;
  const views = dwg?.views ?? [];
  const view = views.find((v) => v.id === selView) ?? null;
  const dimValue = (id: string) => rend?.dimensions.find((d) => d.id === id);
  const hint = TOOLS.find((t) => t.id === tool)!.hint;
  const Y = (y: number) => H - y;

  // preview of the dimension being placed
  const preview = (() => {
    if (!pick?.a || !cursor) return null;
    const A = pick.a;
    if (tool === "radius" || tool === "diameter") return <line x1={A.sx} y1={Y(A.sy)} x2={cursor.x} y2={Y(cursor.y)} stroke="#c8361b" strokeWidth={0.3} strokeDasharray="1.5 1" />;
    if (!pick.b) return <line x1={A.sx} y1={Y(A.sy)} x2={cursor.x} y2={Y(cursor.y)} stroke="#c8361b" strokeWidth={0.25} strokeDasharray="1 1" />;
    const B = pick.b, off = offsetFor(tool, A, B, cursor);
    let u = { x: 1, y: 0 }, n = { x: 0, y: 1 };
    if (tool === "vertical") { u = { x: 0, y: 1 }; n = { x: -1, y: 0 }; }
    if (tool === "aligned") { const L = Math.hypot(B.sx - A.sx, B.sy - A.sy) || 1; u = { x: (B.sx - A.sx) / L, y: (B.sy - A.sy) / L }; n = { x: -u.y, y: u.x }; }
    const base = { x: A.sx + n.x * off, y: A.sy + n.y * off };
    const proj = (P: Snap) => { const t = (P.sx - base.x) * u.x + (P.sy - base.y) * u.y; return { x: base.x + u.x * t, y: base.y + u.y * t }; };
    const pa = proj(A), pb = proj(B);
    return (
      <g stroke="#c8361b" strokeWidth={0.3}>
        <line x1={A.sx} y1={Y(A.sy)} x2={pa.x} y2={Y(pa.y)} strokeDasharray="1 1" />
        <line x1={B.sx} y1={Y(B.sy)} x2={pb.x} y2={Y(pb.y)} strokeDasharray="1 1" />
        <line x1={pa.x} y1={Y(pa.y)} x2={pb.x} y2={Y(pb.y)} />
      </g>
    );
  })();

  const showSnaps = tool !== "select" && tool !== "note";
  const radialTool = tool === "radius" || tool === "diameter";

  return (
    <div className="workspace">
      {/* ------------------------------------------------------------ left */}
      <div className="side left">
        <Section title="Drawings" actions={
          <button className="btn sm" disabled={!parts.length} onClick={async () => {
            const pid = newPart ?? parts[0]?.id;
            const d = await run("creating drawing…", () => api.createDrawing(project.id, { name: `${parts.find((p) => p.id === pid)?.name ?? "Part"} drawing`, part_id: pid }));
            if (d) { await loadList(); setDid(d.id); }
          }}>＋ From part</button>
        } pad={false}>
          <div className="list">
            {list.map((d) => (
              <div key={d.id} className={`item ${d.id === did ? "sel" : ""}`} onClick={() => setDid(d.id)}>
                <span className="name">{d.name}</span><span className="pill">{d.sheet.size}</span>
                <button className="btn ghost sm" onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete drawing "${d.name}"?`)) return;
                  await run("deleting…", () => api.deleteDrawing(d.id));
                  const l = await loadList();
                  if (d.id === did) setDid(l[0]?.id ?? null);
                }}>✕</button>
              </div>
            ))}
            {!list.length && <div className="item muted">No drawings yet — pick a part below and click “From part”.</div>}
          </div>
          <div style={{ padding: "6px 10px 10px" }}>
            <Field label="Part for new drawing / views"><Select value={newPart ?? ""} onChange={(v) => setNewPart(Number(v))} options={parts.map((p) => ({ value: p.id, label: p.name }))} /></Field>
          </div>
        </Section>

        {dwg && (
          <>
            <Section title="Dimension">
              <div className="row wrap">
                {TOOLS.map((t) => <button key={t.id} className={`btn sm ${tool === t.id ? "active" : ""}`} onClick={() => { setTool(t.id); setPick(null); }}>{t.label}</button>)}
              </div>
              <div className="muted small" style={{ marginTop: 6 }}>{hint}{pick?.a ? (pick.b ? " — now place the dimension line." : " — pick the second point.") : ""}</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn sm" onClick={async () => { const r = await apply(() => api.autoDimensions(dwg.id), "auto-dimensioning…"); if (r) notify(`${(r as Drawing & { added: number }).added} dimension(s) added`, "ok"); }}>Auto-dimension</button>
              </div>
            </Section>

            <Section title={`Views (${views.length})`}>
              <div className="list" style={{ margin: "0 -10px" }}>
                {views.map((v) => (
                  <div key={v.id} className={`item ${v.id === selView ? "sel" : ""}`} onClick={() => setSelView(v.id)}>
                    <span className="name">{v.kind}{v.label ? ` · ${v.label}` : ""}</span><span className="pill">{scaleLabel(v.scale)}</span>
                  </div>
                ))}
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                <div className="grow"><Select value={newKind} onChange={setNewKind} options={VIEW_KINDS.map((k) => ({ value: k, label: k }))} /></div>
                <button className="btn sm" disabled={newPart == null} onClick={() => apply(() => api.addView(dwg.id, { part_id: newPart, kind: newKind }), "adding view…")}>＋ View</button>
              </div>
              <button className="btn sm" style={{ marginTop: 6 }} disabled={newPart == null} onClick={() => {
                if (views.length && !confirm("Replace all views with front / top / right / iso of the selected part?")) return;
                apply(() => api.standardViews(dwg.id, newPart!), "laying out views…");
              }}>Standard views</button>
            </Section>

            <Section title="Sheet">
              <div className="col">
                <Field label="Name"><input type="text" defaultValue={dwg.name} key={dwg.id + dwg.name} onBlur={(e) => e.target.value !== dwg.name && patch({ name: e.target.value })} /></Field>
                <div className="row">
                  <div className="grow"><Field label="Size"><Select value={dwg.sheet.size} onChange={(v) => patch({ sheet: { ...dwg.sheet, size: v } })} options={SIZES.map((s) => ({ value: s, label: s }))} /></Field></div>
                  <div className="grow"><Field label="Projection"><Select value={dwg.sheet.projection} onChange={(v) => patch({ sheet: { ...dwg.sheet, projection: v } })} options={[{ value: "third", label: "3rd angle" }, { value: "first", label: "1st angle" }]} /></Field></div>
                </div>
                <Field label="Decimals"><NumField allowExpr={false} value={dwg.sheet.decimals} onChange={(v) => patch({ sheet: { ...dwg.sheet, decimals: Math.max(0, Math.min(4, Math.round(v ?? 2))) } })} /></Field>
                <div className="muted small">Projection changes apply to new standard layouts.</div>
              </div>
            </Section>
          </>
        )}
      </div>

      {/* ------------------------------------------------------------ sheet */}
      <div className="center">
        {dwg && rend ? (
          <svg
            ref={svgRef}
            className="sketch-svg"
            style={{ background: "var(--viewport-bg)", cursor: tool === "select" ? (drag ? "grabbing" : "default") : "crosshair" }}
            viewBox={`${-W * 0.04} ${-H * 0.04} ${W * 1.08} ${H * 1.08}`}
            onWheel={(e) => {
              const svg = svgRef.current!;
              const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
              const u = pt.matrixTransform(svg.getScreenCTM()!.inverse());
              const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
              setCam((c) => ({ k: Math.max(0.3, Math.min(20, c.k * f)), tx: u.x - (u.x - c.tx) * f, ty: u.y - (u.y - c.ty) * f }));
            }}
            onPointerDown={(e) => {
              if (e.button === 1 || e.button === 2) { setPan({ x: e.clientX, y: e.clientY, tx: cam.tx, ty: cam.ty }); (e.target as Element).setPointerCapture?.(e.pointerId); }
            }}
            onPointerMove={(e) => {
              if (pan) {
                const svg = svgRef.current!, m = svg.getScreenCTM()!;
                setCam((c) => ({ ...c, tx: pan.tx + (e.clientX - pan.x) / m.a, ty: pan.ty + (e.clientY - pan.y) / m.d }));
                return;
              }
              const p = toSheet(e.clientX, e.clientY);
              setCursor(p);
              if (drag) setDrag({ ...drag, dx: p.x - drag.from.x, dy: p.y - drag.from.y });
            }}
            onPointerUp={() => {
              setPan(null);
              if (drag && dwg) {
                const { id, ox, oy, dx, dy } = drag;
                setDrag(null);
                if (Math.hypot(dx, dy) > 0.5) patch({ views: dwg.views.map((v) => (v.id === id ? { ...v, x: +(ox + dx).toFixed(2), y: +(oy + dy).toFixed(2) } : v)) });
              }
            }}
            onClick={onClick}
            onContextMenu={(e) => e.preventDefault()}
          >
            <g transform={`translate(${cam.tx} ${cam.ty}) scale(${cam.k})`}>
              <rect x={0} y={0} width={W} height={H} fill="#ffffff" stroke="#9aa0a8" strokeWidth={0.4} style={{ filter: "drop-shadow(0 1px 3px rgba(0,0,0,.25))" }} />
              <Primitives prims={rend.primitives} H={H} dragView={drag ? { id: drag.id, dx: drag.dx, dy: drag.dy, box: rend.views.find((v) => v.id === drag.id)?.box } : null} />
              {rend.views.map((v) => v.box && (
                <rect key={v.id} x={v.box[0] - 3} y={Y(v.box[3]) - 3} width={v.box[2] - v.box[0] + 6} height={v.box[3] - v.box[1] + 6}
                  fill="transparent" stroke={v.id === selView ? "#0b63c5" : "transparent"} strokeWidth={0.4} strokeDasharray="2 1.5"
                  style={{ cursor: tool === "select" ? "move" : "inherit", pointerEvents: tool === "select" ? "all" : "none" }}
                  onPointerDown={(e) => {
                    if (tool !== "select" || e.button !== 0 || !dwg) return;
                    e.stopPropagation();
                    const dv = dwg.views.find((x) => x.id === v.id)!;
                    setSelView(v.id);
                    setDrag({ id: v.id, from: toSheet(e.clientX, e.clientY), ox: dv.x, oy: dv.y, dx: 0, dy: 0 });
                  }} />
              ))}
              {showSnaps && snaps.filter((s) => (radialTool ? s.end === "c" : true) && (!pick?.a || radialTool || s.view === pick.view)).map((s, i) => (
                s.end === "c"
                  ? <circle key={i} cx={s.sx} cy={Y(s.sy)} r={1.2} fill="none" stroke="#0b63c5" strokeWidth={0.35} />
                  : <circle key={i} cx={s.sx} cy={Y(s.sy)} r={0.7} fill="#0b63c5" opacity={0.7} />
              ))}
              {pick?.a && <circle cx={pick.a.sx} cy={Y(pick.a.sy)} r={1.4} fill="#c8361b" />}
              {pick?.b && <circle cx={pick.b.sx} cy={Y(pick.b.sy)} r={1.4} fill="#c8361b" />}
              {preview}
            </g>
          </svg>
        ) : (
          <div className="empty"><div><h3>No drawing</h3>Create a drawing from a part: it gets front, top, right and isometric views, a border and a title block. Add dimensions, export to PDF.</div></div>
        )}
        {dwg && (
          <div className="overlay tr">
            <div className="toolbar">
              <button className="btn ghost sm" onClick={() => setCam({ k: 1, tx: 0, ty: 0 })}>⤢ fit</button>
              <a className="btn ghost sm" href={url(`/api/drawings/${dwg.id}/export?format=pdf`)} download>⇩ PDF</a>
              <a className="btn ghost sm" href={url(`/api/drawings/${dwg.id}/export?format=svg`)} download>⇩ SVG</a>
            </div>
          </div>
        )}
        {cursor && dwg && <div className="overlay bl"><div className="hud mono small">sheet x {cursor.x.toFixed(1)}  y {cursor.y.toFixed(1)} mm</div></div>}
      </div>

      {/* ------------------------------------------------------------ right */}
      <div className="side right">
        {dwg && (
          <>
            {view && (
              <Section title={`View ${view.kind}`} actions={<button className="btn ghost sm" onClick={() => setSelView(null)}>✕</button>}>
                <div className="col">
                  <div className="row">
                    <div className="grow"><Field label="Scale"><Select value={view.scale} onChange={(v) => patch({ views: views.map((x) => (x.id === view.id ? { ...x, scale: Number(v) } : x)) })} options={SCALES.map((s) => ({ value: s, label: scaleLabel(s) }))} /></Field></div>
                    <div className="grow"><Field label="Part"><Select value={view.part_id} onChange={(v) => patch({ views: views.map((x) => (x.id === view.id ? { ...x, part_id: Number(v) } : x)) })} options={parts.map((p) => ({ value: p.id, label: p.name }))} /></Field></div>
                  </div>
                  <Field label="Label"><input type="text" defaultValue={view.label} key={view.id + view.label} onBlur={(e) => e.target.value !== view.label && patch({ views: views.map((x) => (x.id === view.id ? { ...x, label: e.target.value } : x)) })} /></Field>
                  <label className="check"><input type="checkbox" checked={view.hidden} onChange={(e) => patch({ views: views.map((x) => (x.id === view.id ? { ...x, hidden: e.target.checked } : x)) })} />Show hidden lines</label>
                  <label className="check"><input type="checkbox" checked={!!view.show_scale} onChange={(e) => patch({ views: views.map((x) => (x.id === view.id ? { ...x, show_scale: e.target.checked } : x)) })} />Show scale in label</label>
                  <button className="btn sm danger" onClick={() => { setSelView(null); patch({ views: views.filter((x) => x.id !== view.id), dimensions: dwg.dimensions.filter((d) => d.view !== view.id) }); }}>Delete view</button>
                </div>
              </Section>
            )}

            <Section title={`Dimensions (${dwg.dimensions.length})`} pad={false}>
              <table className="t">
                <tbody>
                  {dwg.dimensions.map((d: Json) => {
                    const r = dimValue(d.id);
                    const sym = d.type === "diameter" ? "Ø" : d.type === "radius" ? "R" : d.type === "vertical" ? "↕" : d.type === "horizontal" ? "↔" : "⤢";
                    return (
                      <tr key={d.id} title={r?.error ?? (r?.ok ? "attached to the model" : "reference lost: re-attached to its last known position")}>
                        <td style={{ width: 18 }}><span className={`dot ${r && !r.ok ? "bad" : ""}`} /></td>
                        <td>{sym} {d.prefix ?? ""}{r?.value != null ? fmt(r.value, dwg.sheet.decimals) : "–"}<div className="muted small">{views.find((v) => v.id === d.view)?.kind ?? d.view}</div></td>
                        <td style={{ width: 70 }}><input type="text" placeholder="±tol" defaultValue={d.tolerance ?? ""} key={d.id + (d.tolerance ?? "")}
                          onBlur={(e) => { const t = parseFloat(e.target.value); patch({ dimensions: dwg.dimensions.map((x: Json) => (x.id === d.id ? { ...x, tolerance: Number.isFinite(t) && t > 0 ? t : undefined } : x)) }); }} /></td>
                        <td style={{ width: 28 }}><button className="btn ghost sm" onClick={() => patch({ dimensions: dwg.dimensions.filter((x: Json) => x.id !== d.id) })}>✕</button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!dwg.dimensions.length && <div className="muted small" style={{ padding: 10 }}>None — use the dimension tools or Auto-dimension.</div>}
              <div className="muted small" style={{ padding: "6px 10px 10px" }}>Dimensions are attached to model edges and re-measured whenever the part changes.</div>
            </Section>

            <Section title={`Notes (${dwg.notes.length})`}>
              <div className="col">
                {dwg.notes.map((n, i) => (
                  <div key={i} className="row">
                    <textarea defaultValue={n.text} key={i + n.text} style={{ minHeight: 40 }} onBlur={(e) => e.target.value !== n.text && patch({ notes: dwg.notes.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })} />
                    <button className="btn ghost sm" onClick={() => patch({ notes: dwg.notes.filter((_, j) => j !== i) })}>✕</button>
                  </div>
                ))}
                <button className="btn sm" onClick={() => patch({ notes: [...dwg.notes, { x: 20, y: 45 + dwg.notes.length * 12, text: "NOTES:\n1. Remove all burrs and sharp edges." }] })}>＋ Note</button>
              </div>
            </Section>

            <Section title="Title block">
              <div className="col">
                {TITLE_FIELDS.map(([k, label]) => (
                  <Field key={k} label={label}><input type="text" defaultValue={dwg.title[k] ?? ""} key={dwg.id + k + (dwg.title[k] ?? "")} onBlur={(e) => e.target.value !== (dwg.title[k] ?? "") && patch({ title: { ...dwg.title, [k]: e.target.value } })} /></Field>
                ))}
              </div>
            </Section>
          </>
        )}
      </div>
    </div>
  );
}

/** Drawing primitives from the backend (sheet mm, y up) as SVG. A dragged view is drawn offset while dragging. */
function Primitives({ prims, H, dragView }: { prims: Json[]; H: number; dragView: { id: string; dx: number; dy: number; box?: number[] } | null }) {
  const inBox = (pts: number[][]) => {
    const b = dragView?.box;
    if (!b) return false;
    return pts.every(([x, y]) => x >= b[0] - 30 && x <= b[2] + 30 && y >= b[1] - 30 && y <= b[3] + 30);
  };
  return (
    <g>
      {prims.map((p: Json, i: number) => {
        const shift = dragView && (p.layer === "visible" || p.layer === "hidden" || p.layer === "tangent" || p.layer === "centre" || p.layer === "label" || p.layer === "dimension")
          && inBox(p.t === "text" ? [[p.x, p.y]] : p.pts);
        const tr = shift ? `translate(${dragView!.dx} ${-dragView!.dy})` : undefined;
        if (p.t === "line") {
          return <polyline key={i} transform={tr} points={p.pts.map(([x, y]: number[]) => `${x},${H - y}`).join(" ")} fill="none" stroke={p.c} strokeWidth={p.w}
            strokeDasharray={p.dash ? p.dash.join(" ") : undefined} strokeLinecap="round" strokeLinejoin="round" />;
        }
        if (p.t === "fill") return <polygon key={i} transform={tr} points={p.pts.map(([x, y]: number[]) => `${x},${H - y}`).join(" ")} fill={p.c} />;
        return (
          <text key={i} transform={(tr ?? "") + (p.rot ? ` rotate(${-p.rot} ${p.x} ${H - p.y})` : "")} x={p.x} y={H - p.y} fontSize={p.s} fill={p.c}
            fontFamily="Helvetica, Arial, sans-serif" fontWeight={p.bold ? 700 : 400} textAnchor={p.anchor} style={{ userSelect: "none" }}>{p.text}</text>
        );
      })}
    </g>
  );
}
