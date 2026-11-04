import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, url } from "../api";
import { ParamForm } from "../components/ParamForm";
import { Viewport } from "../components/Viewport";
import { Field, NumField, Section, Select, VecField, download, fmt, fmtTime } from "../components/ui";
import { useApp } from "../store";
import type { CamOp, CamSetup, Json, MeshData, Tool, Toolpath } from "../types";

const KIND_COLOR: Record<string, string> = { rapid: "#f5a623", cut: "#3ec1d3", plunge: "#ef5b5b" };

export default function CamWorkspace() {
  const { meta, project, parts, sketches, partId, setPartId, notify, run } = useApp();
  const part = parts.find((p) => p.id === partId) ?? null;
  const [mesh, setMesh] = useState<MeshData | null>(null);
  const [setups, setSetups] = useState<CamSetup[]>([]);
  const [sid, setSid] = useState<number | null>(null);
  const [setup, setSetup] = useState<CamSetup | null>(null);
  const [selOp, setSelOp] = useState<string | null>(null);
  const [gen, setGen] = useState<Json>(null);
  const [simRes, setSimRes] = useState<Json>(null);
  const [showKinds, setShowKinds] = useState({ rapid: true, cut: true, plunge: true });
  const [progress, setProgress] = useState(1);
  const [simMode, setSimMode] = useState<"height" | "error">("error");
  const [showPart, setShowPart] = useState(true);
  const [showSim, setShowSim] = useState(true);
  const [tab, setTab] = useState<"ops" | "tools" | "stock" | "post">("ops");
  const saveTimer = useRef<number>();

  // part mesh for context
  useEffect(() => {
    setMesh(null);
    if (partId == null) return;
    api.partMesh(partId).then((r) => setMesh(r.mesh)).catch((e) => notify(String(e)));
  }, [partId, notify]);

  const loadSetups = useCallback(async () => {
    if (partId == null) { setSetups([]); return []; }
    const l = await api.camSetups(partId);
    setSetups(l);
    return l;
  }, [partId]);

  useEffect(() => {
    setSid(null); setSetup(null); setGen(null); setSimRes(null);
    loadSetups().then((l) => setSid(l[0]?.id ?? null)).catch((e) => notify(String(e)));
  }, [loadSetups, notify]);

  useEffect(() => {
    setSelOp(null); setGen(null); setSimRes(null);
    if (sid == null) { setSetup(null); return; }
    api.camSetup(sid).then(setSetup).catch((e) => notify(String(e)));
  }, [sid, notify]);

  const save = (next: CamSetup, immediate = false) => {
    setSetup(next);
    window.clearTimeout(saveTimer.current);
    const go = () => api.updateCam(next.id, { name: next.name, stock: next.stock, tools: next.tools, operations: next.operations, post: next.post }).catch((e) => notify(String(e)));
    if (immediate) go(); else saveTimer.current = window.setTimeout(go, 500);
  };

  const toolpaths: Toolpath[] = gen?.toolpaths ?? [];
  const segCount = useMemo(() => toolpaths.reduce((n, t) => n + Math.max(0, t.pts.length / 3 - 1), 0), [toolpaths]);

  if (!project || !meta) return <div className="empty"><div><h3>No project</h3></div></div>;
  const op = setup?.operations.find((o) => o.id === selOp) ?? null;
  const stock = gen?.stock ?? simRes?.stock;

  const updateOp = (id: string, patch: Partial<CamOp>) => setup && save({ ...setup, operations: setup.operations.map((o) => (o.id === id ? { ...o, ...patch } : o)) });
  const nextOpId = () => { const used = new Set(setup?.operations.map((o) => o.id)); let n = 1; while (used.has(`op${n}`)) n++; return `op${n}`; };

  const generate = async () => {
    if (!setup) return;
    window.clearTimeout(saveTimer.current);
    await api.updateCam(setup.id, { stock: setup.stock, tools: setup.tools, operations: setup.operations, post: setup.post });
    const r = await run("generating toolpaths…", () => api.camGenerate(setup.id));
    if (r) { setGen(r); setProgress(1); r.errors?.forEach((e: string) => notify(e)); }
  };
  const simulate = async () => {
    if (!setup) return;
    window.clearTimeout(saveTimer.current);
    await api.updateCam(setup.id, { stock: setup.stock, tools: setup.tools, operations: setup.operations, post: setup.post });
    const r = await run("simulating material removal…", () => api.camSimulate(setup.id));
    if (r) { setSimRes(r); notify("Simulation complete", "ok"); }
  };

  return (
    <div className="workspace wide-right">
      <div className="side left">
        <Section title="Part to machine" pad>
          <Select value={partId ?? ""} onChange={(v) => setPartId(Number(v))} options={parts.map((p) => ({ value: p.id, label: p.name }))} />
        </Section>
        <Section title="CAM setups" actions={
          <button className="btn sm" disabled={partId == null} onClick={async () => {
            const s = await run("creating…", () => api.createCam(partId!, `Setup ${setups.length + 1}`));
            if (s) { await loadSetups(); setSid(s.id); }
          }}>＋ New</button>
        } pad={false}>
          <div className="list">
            {setups.map((s) => (
              <div key={s.id} className={`item ${s.id === sid ? "sel" : ""}`} onClick={() => setSid(s.id)}>
                <span className="name">{s.name}</span><span className="pill">{s.operations.length} ops</span>
                <button className="btn ghost sm" onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete setup "${s.name}"?`)) return;
                  await run("deleting…", () => api.deleteCam(s.id));
                  const l = await loadSetups();
                  if (s.id === sid) setSid(l[0]?.id ?? null);
                }}>✕</button>
              </div>
            ))}
            {!setups.length && <div className="item muted">No setups for this part</div>}
          </div>
        </Section>

        {setup && (
          <>
            <Section title="Operations" actions={
              <>
                <button className="btn sm" title="Recognise holes, floors and the outer profile" onClick={async () => {
                  const s = await run("recognising features…", () => api.camAuto(setup.id));
                  if (s) { setSetup(s); setSelOp(s.operations[0]?.id ?? null); }
                }}>⚙ Auto</button>
                <select style={{ width: 96 }} value="" onChange={(e) => {
                  const t = e.target.value; if (!t) return;
                  const sch = meta.cam_operations[t];
                  const params = Object.fromEntries(sch.params.map((p) => [p.name, p.default]));
                  params.tool = setup.tools.find((x) => x.type === (t === "drill" ? "drill" : "flat"))?.id ?? setup.tools[0]?.id;
                  const o: CamOp = { id: nextOpId(), type: t, name: sch.label, enabled: true, params };
                  save({ ...setup, operations: [...setup.operations, o] }, true);
                  setSelOp(o.id);
                }}>
                  <option value="">＋ Add…</option>
                  {Object.entries(meta.cam_operations).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                </select>
              </>
            } pad={false}>
              <div className="list">
                {setup.operations.map((o, i) => {
                  const info = gen?.summary?.operations?.find?.((p: Json) => p.op === o.id);
                  return (
                    <div key={o.id} className={`item ${o.id === selOp ? "sel" : ""}`} onClick={() => setSelOp(o.id)}>
                      <input type="checkbox" checked={o.enabled} onClick={(e) => e.stopPropagation()} onChange={(e) => updateOp(o.id, { enabled: e.target.checked })} />
                      <span className="name" style={{ opacity: o.enabled ? 1 : 0.5 }}>{i + 1}. {o.name ?? o.type}</span>
                      {info && <span className="pill">{fmtTime(info.time_s)}</span>}
                      <button className="btn ghost sm" disabled={i === 0} onClick={(e) => { e.stopPropagation(); const a = [...setup.operations]; [a[i - 1], a[i]] = [a[i], a[i - 1]]; save({ ...setup, operations: a }, true); }}>↑</button>
                      <button className="btn ghost sm" disabled={i === setup.operations.length - 1} onClick={(e) => { e.stopPropagation(); const a = [...setup.operations]; [a[i + 1], a[i]] = [a[i], a[i + 1]]; save({ ...setup, operations: a }, true); }}>↓</button>
                      <button className="btn ghost sm" onClick={(e) => { e.stopPropagation(); save({ ...setup, operations: setup.operations.filter((x) => x.id !== o.id) }, true); if (selOp === o.id) setSelOp(null); }}>✕</button>
                    </div>
                  );
                })}
                {!setup.operations.length && <div className="item muted">Use ⚙ Auto or ＋ Add</div>}
              </div>
            </Section>
          </>
        )}
      </div>

      <div className="center">
        <Viewport
          deps={[mesh, gen, simRes, showKinds, progress, simMode, showPart, showSim, stock]}
          fitOn={mesh ? partId : null}
          setup={(v) => {
            v.clear();
            if (mesh && showPart) v.setSolid("part", mesh, { color: part?.color, opacity: simRes && showSim ? 0.25 : 0.85, edges: false, pickable: false });
            if (stock) v.setBox("stock", stock.min ?? [stock.xmin, stock.ymin, stock.zmin], stock.max ?? [stock.xmax, stock.ymax, stock.zmax], 0x6b7690);
            if (simRes?.grid && showSim) v.setHeightfield("sim", simRes.grid, simMode);
            if (toolpaths.length) {
              let budget = Math.floor(segCount * progress);
              const bucket: Record<string, number[]> = { rapid: [], cut: [], plunge: [] };
              for (const t of toolpaths) {
                if (!showKinds[t.kind] || budget <= 0) continue;
                const n = t.pts.length / 3;
                for (let i = 0; i < n - 1 && budget > 0; i++, budget--) bucket[t.kind].push(...t.pts.slice(i * 3, i * 3 + 6));
              }
              (Object.keys(bucket) as (keyof typeof bucket)[]).forEach((k) => bucket[k].length && v.setSegments(`tp-${k}`, bucket[k], KIND_COLOR[k], k === "rapid" ? 0.6 : 1));
            }
            (gen?.collisions?.collisions ?? []).slice(0, 80).forEach((c: Json, i: number) => v.setMarker(`col${i}`, [c.x, c.y, c.z], 1.6, c.kind === "rapid" ? 0xee8426 : 0xc8361b));
          }}
        >
          <div className="overlay tl">
            <div className="hud">
              <b>{setup?.name ?? "No setup"}</b>
              {gen && <div className="small muted">{toolpaths.length} paths · {segCount.toLocaleString()} segments</div>}
              <div className="row wrap" style={{ marginTop: 4 }}>
                {(["rapid", "cut", "plunge"] as const).map((k) => (
                  <label key={k} className="check small"><input type="checkbox" checked={showKinds[k]} onChange={(e) => setShowKinds({ ...showKinds, [k]: e.target.checked })} /><span className="swatch" style={{ background: KIND_COLOR[k] }} />{k}</label>
                ))}
                <label className="check small"><input type="checkbox" checked={showPart} onChange={(e) => setShowPart(e.target.checked)} />part</label>
                {simRes && <label className="check small"><input type="checkbox" checked={showSim} onChange={(e) => setShowSim(e.target.checked)} />stock</label>}
              </div>
            </div>
          </div>
          {toolpaths.length > 0 && (
            <div className="overlay bc">
              <div className="hud row" style={{ width: 360 }}>
                <span className="small muted">Playback</span>
                <input type="range" min={0} max={1} step={0.005} value={progress} onChange={(e) => setProgress(Number(e.target.value))} style={{ flex: 1 }} />
                <span className="mono small">{Math.round(progress * 100)}%</span>
              </div>
            </div>
          )}
          {simRes?.grid && showSim && (
            <div className="overlay br">
              <div className="hud legend">
                <div className="row"><b className="small">Stock view</b><span className="grow" />
                  <button className={`btn sm ${simMode === "error" ? "active" : ""}`} onClick={() => setSimMode("error")}>error</button>
                  <button className={`btn sm ${simMode === "height" ? "active" : ""}`} onClick={() => setSimMode("height")}>height</button>
                </div>
                {simMode === "error"
                  ? <div className="small" style={{ marginTop: 4 }}><span className="swatch" style={{ background: "#e62633", display: "inline-block" }} /> gouge &nbsp;<span className="swatch" style={{ background: "#3373f2", display: "inline-block" }} /> leftover &nbsp;<span className="swatch" style={{ background: "#59cc80", display: "inline-block" }} /> on target</div>
                  : <><div className="bar" /><div className="ticks"><span>low</span><span>high</span></div></>}
              </div>
            </div>
          )}
        </Viewport>
      </div>

      <div className="side right">
        {setup && (
          <>
            <Section title="Generate" >
              <div className="row wrap">
                <button className="btn primary sm" onClick={generate}>Generate toolpaths</button>
                <button className="btn sm" onClick={simulate}>Simulate removal</button>
                {gen?.gcode && <button className="btn sm" onClick={() => download(`${setup.name.replace(/\s+/g, "_")}.nc`, gen.gcode)}>⇩ G-code</button>}
              </div>
              {gen?.errors?.map((e: string, i: number) => <div key={i} className="notice err" style={{ marginTop: 6 }}>{e}</div>)}
              {gen?.warnings?.map((e: string, i: number) => <div key={i} className="notice warn" style={{ marginTop: 6 }}>{e}</div>)}
              {gen?.summary && (
                <div className="kv" style={{ marginTop: 8 }}>
                  <span>Estimated time</span><span>{fmtTime(gen.summary.estimated_time_s ?? 0)}</span>
                  <span>Cutting length</span><span>{fmt((gen.summary.cut_length_mm ?? 0) / 1000, 2)} m</span>
                  <span>Rapid length</span><span>{fmt((gen.summary.rapid_length_mm ?? 0) / 1000, 2)} m</span>
                  <span>G-code lines</span><span>{gen.summary.lines ?? gen.gcode?.split("\n").length}</span>
                  {gen.collisions && <><span>Holder / shank / rapid collisions</span><span style={{ color: gen.collisions.count ? "var(--danger)" : "var(--ok)" }}>{gen.collisions.count ? gen.collisions.count : "none"}</span></>}
                </div>
              )}
              {gen?.collisions?.collisions?.length > 0 && (
                <table className="t" style={{ marginTop: 6 }}>
                  <thead><tr><th>Op</th><th>What</th><th className="num">Tip X, Y, Z</th><th className="num">Depth</th></tr></thead>
                  <tbody>{gen.collisions.collisions.slice(0, 12).map((c: Json, i: number) => (
                    <tr key={i}><td>{c.op}</td><td style={{ color: "var(--danger)" }}>{c.kind}</td><td className="num">{[c.x, c.y, c.z].map((x: number) => x.toFixed(1)).join(", ")}</td><td className="num">{fmt(c.depth_mm, 2)}</td></tr>
                  ))}</tbody>
                </table>
              )}
              {simRes && (
                <div className="kv" style={{ marginTop: 8 }}>
                  <span>Removed</span><span>{fmt(simRes.removed_volume_mm3 / 1000)} cm³</span>
                  <span>Leftover</span><span>{fmt(simRes.leftover_volume_mm3 / 1000, 3)} cm³</span>
                  <span>Gouged</span><span style={{ color: simRes.gouge_volume_mm3 > 1 ? "var(--danger)" : undefined }}>{fmt(simRes.gouge_volume_mm3 / 1000, 3)} cm³</span>
                  <span>Resolution</span><span>{fmt(simRes.resolution_mm, 2)} mm</span>
                </div>
              )}
            </Section>

            <div className="row" style={{ padding: "8px 12px", gap: 4 }}>
              {(["ops", "tools", "stock", "post"] as const).map((t) => <button key={t} className={`btn sm ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{{ ops: "Operation", tools: "Tools", stock: "Stock", post: "Post" }[t]}</button>)}
            </div>

            {tab === "ops" && (op ? (
              <Section title={`${meta.cam_operations[op.type]?.label ?? op.type}`}>
                <div className="col">
                  <Field label="Name"><input type="text" value={op.name ?? ""} onChange={(e) => updateOp(op.id, { name: e.target.value })} /></Field>
                  <ParamForm defs={meta.cam_operations[op.type]?.params ?? []} values={op.params} onChange={(k, v) => updateOp(op.id, { params: { ...op.params, [k]: v } })} ctx={{ tools: setup.tools, sketches }} />
                </div>
              </Section>
            ) : <div className="muted small" style={{ padding: 12 }}>Select an operation to edit its parameters.</div>)}

            {tab === "tools" && <ToolsEditor tools={setup.tools} onChange={(tools) => save({ ...setup, tools }, false)} />}

            {tab === "stock" && (
              <Section title="Stock">
                <div className="col">
                  <label className="check"><input type="checkbox" checked={!!setup.stock?.min} onChange={(e) => {
                    const b = mesh ? bbox(mesh) : null;
                    save({ ...setup, stock: e.target.checked ? { min: b?.min ?? [0, 0, 0], max: b?.max ?? [100, 100, 20] } : { margin: [2, 2], top_extra: 1 } }, true);
                  }} />Explicit bounding box</label>
                  {setup.stock?.min ? (
                    <>
                      <Field label="Min [x,y,z]"><VecField n={3} value={setup.stock.min} onChange={(v) => save({ ...setup, stock: { ...setup.stock, min: v } })} /></Field>
                      <Field label="Max [x,y,z]"><VecField n={3} value={setup.stock.max} onChange={(v) => save({ ...setup, stock: { ...setup.stock, max: v } })} /></Field>
                    </>
                  ) : (
                    <>
                      <Field label="Side margin [x, y] (mm)"><VecField n={2} value={setup.stock?.margin ?? [2, 2]} onChange={(v) => save({ ...setup, stock: { ...setup.stock, margin: v } })} /></Field>
                      <Field label="Extra above part (mm)"><NumField allowExpr={false} value={setup.stock?.top_extra ?? 1} onChange={(v) => save({ ...setup, stock: { ...setup.stock, top_extra: v ?? 0 } })} /></Field>
                    </>
                  )}
                  <div className="muted small">Work zero: X/Y as modelled, Z=0 at the model origin plane. Stock top is the Z reference for clearance.</div>
                </div>
              </Section>
            )}

            {tab === "post" && (
              <Section title="Post processor">
                <div className="col">
                  <Field label="Dialect"><Select value={setup.post?.dialect ?? "fanuc"} onChange={(v) => save({ ...setup, post: { ...setup.post, dialect: v } }, true)} options={meta.post_dialects.map((d) => ({ value: d, label: d }))} /></Field>
                  <Field label="Program name"><input type="text" value={setup.post?.program_name ?? ""} onChange={(e) => save({ ...setup, post: { ...setup.post, program_name: e.target.value } })} /></Field>
                  <Field label="Work offset"><input type="text" value={setup.post?.work_offset ?? "G54"} onChange={(e) => save({ ...setup, post: { ...setup.post, work_offset: e.target.value } })} /></Field>
                  <Field label="Coolant code (blank = none)"><input type="text" value={setup.post?.coolant ?? ""} onChange={(e) => save({ ...setup, post: { ...setup.post, coolant: e.target.value } })} /></Field>
                  <div className="row">
                    <Field label="Clearance (mm)"><NumField allowExpr={false} value={setup.post?.clearance ?? 3} onChange={(v) => save({ ...setup, post: { ...setup.post, clearance: v ?? 3 } })} /></Field>
                    <Field label="Safe height (mm)"><NumField allowExpr={false} value={setup.post?.safe_height ?? 25} onChange={(v) => save({ ...setup, post: { ...setup.post, safe_height: v ?? 25 } })} /></Field>
                  </div>
                  <label className="check"><input type="checkbox" checked={!!setup.post?.line_numbers} onChange={(e) => save({ ...setup, post: { ...setup.post, line_numbers: e.target.checked } }, true)} />Line numbers</label>
                </div>
              </Section>
            )}

            {gen?.gcode && (
              <Section title="G-code" pad={false} actions={<a className="btn sm" href={url(`/api/cam/${setup.id}/gcode`)} download>⇩ .nc</a>}>
                <pre className="code">{gen.gcode.split("\n").slice(0, 400).join("\n")}{gen.gcode.split("\n").length > 400 ? "\n…" : ""}</pre>
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function bbox(m: MeshData) {
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < m.positions.length; i += 3) for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], m.positions[i + k]); max[k] = Math.max(max[k], m.positions[i + k]); }
  return { min: min.map((x) => x - 2), max: max.map((x, k) => x + (k === 2 ? 1 : 2)) };
}

function ToolsEditor({ tools, onChange }: { tools: Tool[]; onChange: (t: Tool[]) => void }) {
  const set = (id: number, patch: Partial<Tool>) => onChange(tools.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  return (
    <Section title="Tool library" actions={
      <button className="btn sm" onClick={() => onChange([...tools, { id: Math.max(0, ...tools.map((t) => t.id)) + 1, name: "New end mill", type: "flat", diameter: 6, flutes: 3, rpm: 10000, feed: 1000, plunge_feed: 300 }])}>＋ Tool</button>
    }>
      <div className="col" style={{ gap: 12 }}>
        {tools.map((t) => (
          <div key={t.id} className="col" style={{ gap: 5, paddingBottom: 10, borderBottom: "1px solid var(--border)" }}>
            <div className="row">
              <span className="pill">T{t.id}</span>
              <input type="text" value={t.name} onChange={(e) => set(t.id, { name: e.target.value })} />
              <button className="btn ghost sm" onClick={() => onChange(tools.filter((x) => x.id !== t.id))}>✕</button>
            </div>
            <div className="row">
              <div className="grow"><Select value={t.type} onChange={(v) => set(t.id, { type: v })} options={[{ value: "flat", label: "Flat end mill" }, { value: "ball", label: "Ball end mill" }, { value: "drill", label: "Drill" }]} /></div>
              <div className="grow"><Field label="Ø mm"><NumField allowExpr={false} value={t.diameter} onChange={(v) => set(t.id, { diameter: v ?? 1 })} /></Field></div>
            </div>
            <div className="vec v3">
              <Field label="RPM"><NumField allowExpr={false} value={t.rpm} onChange={(v) => set(t.id, { rpm: v ?? 0 })} /></Field>
              <Field label="Feed"><NumField allowExpr={false} value={t.feed} onChange={(v) => set(t.id, { feed: v ?? 0 })} /></Field>
              <Field label="Plunge"><NumField allowExpr={false} value={t.plunge_feed} onChange={(v) => set(t.id, { plunge_feed: v ?? 0 })} /></Field>
            </div>
            <div className="vec v2" title="Used by the collision check: shank above the flutes and the holder face at the stickout length">
              <Field label="Flute length"><NumField allowExpr={false} value={t.flute_length ?? 3 * t.diameter} onChange={(v) => set(t.id, { flute_length: v ?? undefined })} /></Field>
              <Field label="Stickout (tip → holder)"><NumField allowExpr={false} value={t.stickout ?? 5 * t.diameter} onChange={(v) => set(t.id, { stickout: v ?? undefined })} /></Field>
              <Field label="Shank Ø"><NumField allowExpr={false} value={t.shank_diameter ?? t.diameter} onChange={(v) => set(t.id, { shank_diameter: v ?? undefined })} /></Field>
              <Field label="Holder Ø"><NumField allowExpr={false} value={t.holder_diameter ?? Math.max(2.5 * t.diameter, t.diameter + 10)} onChange={(v) => set(t.id, { holder_diameter: v ?? undefined })} /></Field>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}
