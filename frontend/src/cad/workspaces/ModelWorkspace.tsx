import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, url } from "../api";
import { ParamForm } from "../components/ParamForm";
import { Viewport } from "../components/Viewport";
import { Field, NumField, Section, Select, fmt } from "../components/ui";
import { useApp } from "../store";
import type { Json, MassProps, MeshData, Part } from "../types";

export default function ModelWorkspace() {
  const { meta, project, parts, sketches, partId, setPartId, upsertPart, refreshParts, notify, run, busy } = useApp();
  const part = parts.find((p) => p.id === partId) ?? null;
  const [mesh, setMesh] = useState<MeshData | null>(null);
  const [props, setProps] = useState<MassProps | null>(null);
  const [selFeature, setSelFeature] = useState<string | null>(null);
  const [pickedFace, setPickedFace] = useState<number | null>(null);
  const [pickedEdge, setPickedEdge] = useState<number | null>(null);
  const [pickRefs, setPickRefs] = useState<{ face?: string; edge?: string }>({});
  const [pickInfo, setPickInfo] = useState<string>("");
  const [addType, setAddType] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const seq = useRef(0);

  const loadMesh = useCallback(async (id: number) => {
    const my = ++seq.current;
    try {
      const r = await api.partMesh(id);
      if (my !== seq.current) return;
      setMesh(r.mesh);
      setProps(r.properties);
      if (r.part) upsertPart(r.part);
    } catch (e) { notify(String(e)); }
  }, [notify, upsertPart]);

  useEffect(() => {
    setPickedFace(null); setPickedEdge(null); setPickInfo(""); setSelFeature(null);
    if (partId == null) { setMesh(null); setProps(null); return; }
    loadMesh(partId);
  }, [partId, loadMesh]);

  // turn the picked face / edge index into persistent references (indices change when upstream features do)
  useEffect(() => {
    setPickRefs({});
    if (partId == null || pickedFace == null) return;
    let live = true;
    Promise.all([
      api.partReference(partId, "face", [pickedFace]).then((r) => r.selector).catch(() => `idx:${pickedFace}`),
      pickedEdge != null ? api.partReference(partId, "edge", [pickedEdge]).then((r) => r.selector).catch(() => undefined) : Promise.resolve(undefined),
    ]).then(([face, edge]) => { if (live) setPickRefs({ face, edge }); });
    return () => { live = false; };
  }, [partId, pickedFace, pickedEdge, mesh]);

  const feature = part?.features.find((f) => f.id === selFeature) ?? null;
  const status = (id: string) => part?.statuses?.find((s) => s.id === id);

  const apply = async (fn: () => Promise<Part>) => {
    const p = await run("rebuilding…", fn);
    if (p) { upsertPart(p); if (partId != null) await loadMesh(partId); }
    return p;
  };

  const groups = useMemo(() => {
    const g: Record<string, [string, string][]> = {};
    Object.entries(meta?.features ?? {}).forEach(([k, v]) => (g[v.group] ??= []).push([k, v.label]));
    return g;
  }, [meta]);

  if (!project || !meta) return <div className="empty"><div><h3>No project</h3>Create or open a project first.</div></div>;

  const patchParam = (name: string, v: Json) => {
    if (!part || !feature) return;
    const my = feature.id;
    // optimistic local update so inputs do not jump
    upsertPart({ ...part, features: part.features.map((f) => (f.id === my ? { ...f, params: { ...f.params, [name]: v } } : f)) });
    apply(() => api.updateFeature(part.id, my, { params: { [name]: v } }));
  };

  const setParameters = (params: Record<string, Json>) => part && apply(() => api.updatePart(part.id, { parameters: params }));

  return (
    <div className="workspace">
      {/* ------------------------------------------------ left: parts + feature tree */}
      <div className="side left">
        <Section title="Parts" actions={
          <>
            <button className="btn sm" onClick={async () => { const p = await run("creating…", () => api.createPart(project.id, `Part ${parts.length + 1}`)); if (p) { await refreshParts(); setPartId(p.id); } }}>＋ New</button>
            <button className="btn sm" title="Import STEP" onClick={() => fileRef.current?.click()}>⇪ STEP</button>
            <input ref={fileRef} type="file" accept=".step,.stp" hidden onChange={async (e) => {
              const f = e.target.files?.[0]; e.target.value = "";
              if (!f) return;
              const p = await run("importing…", () => api.importStep(project.id, f));
              if (p) { await refreshParts(); setPartId(p.id); }
            }} />
          </>
        } pad={false}>
          <div className="list">
            {parts.map((p) => (
              <div key={p.id} className={`item ${p.id === partId ? "sel" : ""}`} onClick={() => setPartId(p.id)}>
                <span className="swatch" style={{ background: p.color }} />
                <span className="name">{p.name}</span>
                <button className="btn ghost sm" title="Duplicate" onClick={async (e) => { e.stopPropagation(); const q = await run("duplicating…", () => api.duplicatePart(p.id)); if (q) { await refreshParts(); setPartId(q.id); } }}>⧉</button>
                <button className="btn ghost sm" title="Delete" onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete part "${p.name}"?`)) return;
                  await run("deleting…", () => api.deletePart(p.id));
                  const rest = await refreshParts();
                  if (p.id === partId) setPartId(rest[0]?.id ?? null);
                }}>✕</button>
              </div>
            ))}
            {!parts.length && <div className="item muted">No parts yet</div>}
          </div>
        </Section>

        {part && (
          <Section title="Feature tree" pad={false} actions={
            <select style={{ width: 120 }} value={addType} onChange={async (e) => {
              const t = e.target.value; setAddType("");
              if (!t) return;
              const sk = sketches[0]?.id;
              const base = meta.features[t].params.some((p) => p.type === "sketch") && sk ? { [meta.features[t].params.find((p) => p.type === "sketch")!.name]: sk } : {};
              const r = await apply(() => api.addFeature(part.id, t, base));
              if (r) setSelFeature(r.features[r.features.length - 1].id);
            }}>
              <option value="">＋ Add feature…</option>
              {Object.entries(groups).map(([g, items]) => (
                <optgroup key={g} label={g}>{items.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</optgroup>
              ))}
            </select>
          }>
            <div className="list">
              {part.features.map((f, i) => {
                const st = status(f.id);
                return (
                  <div key={f.id} className={`item ${f.id === selFeature ? "sel" : ""} ${f.suppressed ? "suppressed" : ""}`} onClick={() => setSelFeature(f.id)} title={st?.error ?? ""}>
                    <span className={`dot ${f.suppressed ? "off" : st && !st.ok ? "bad" : st?.warnings?.length ? "warn" : ""}`} />
                    <span className="name">{f.name}</span>
                    <button className="btn ghost sm" title="Move up" disabled={i === 0} onClick={(e) => { e.stopPropagation(); apply(() => api.moveFeature(part.id, f.id, i - 1)); }}>↑</button>
                    <button className="btn ghost sm" title="Move down" disabled={i === part.features.length - 1} onClick={(e) => { e.stopPropagation(); apply(() => api.moveFeature(part.id, f.id, i + 1)); }}>↓</button>
                    <button className="btn ghost sm" title={f.suppressed ? "Unsuppress" : "Suppress"} onClick={(e) => { e.stopPropagation(); apply(() => api.updateFeature(part.id, f.id, { suppressed: !f.suppressed })); }}>{f.suppressed ? "◌" : "●"}</button>
                    <button className="btn ghost sm" title="Delete" onClick={(e) => { e.stopPropagation(); if (selFeature === f.id) setSelFeature(null); apply(() => api.deleteFeature(part.id, f.id)); }}>✕</button>
                  </div>
                );
              })}
              {!part.features.length && <div className="item muted">Empty — add a Box, or Extrude a sketch</div>}
            </div>
            {part.features.some((f) => Object.values(f.params).some((v) => typeof v === "string" && /^(idx|near):/.test(v))) && (
              <div className="row" style={{ padding: "8px 10px" }}>
                <span className="muted small grow">Some features use index / point selectors that break when upstream features change.</span>
                <button className="btn sm" onClick={async () => {
                  const r = await apply(() => api.stabilizeRefs(part.id));
                  if (r) notify(`${(r as Part & { changed: number }).changed} feature(s) now use persistent references`, "ok");
                }}>Make persistent</button>
              </div>
            )}
          </Section>
        )}
      </div>

      {/* ------------------------------------------------ centre: viewport */}
      <div className="center">
        <Viewport
          deps={[mesh, part?.color, pickedFace]}
          fitOn={mesh ? partId : null}
          setup={(v) => {
            v.clear();
            if (mesh) { v.setSolid("part", mesh, { color: part?.color }); v.highlightFaces("part", pickedFace != null ? [pickedFace] : []); }
            v.onPick((h) => {
              if (!h || !h.face) { setPickedFace(null); setPickedEdge(null); setPickInfo(""); return; }
              const p = h.point.toArray();
              const edge = mesh ? nearestEdge(mesh, p) : null;
              setPickedFace(h.face.index);
              setPickedEdge(edge?.index ?? null);
              setPickInfo(`face ${h.face.index} · ${h.face.type} · ${fmt(h.face.area)} mm²` + (edge ? ` · nearest edge ${edge.index} (${edge.type.toLowerCase()})` : "") + ` · pt ${p.map((x) => x.toFixed(1)).join(", ")}`);
            });
          }}
        >
          <div className="overlay tl">
            <div className="hud">
              {part ? <><b>{part.name}</b> <span className="muted">· click a face to pick it as a selector</span></> : "Select or create a part"}
              {pickInfo && <div className="mono small hl" style={{ marginTop: 3 }}>{pickInfo}</div>}
              {busy && <div className="muted small">⏳ {busy}</div>}
            </div>
          </div>
        </Viewport>
      </div>

      {/* ------------------------------------------------ right: properties */}
      <div className="side right">
        {part && (
          <>
            <Section title="Part">
              <div className="col">
                <Field label="Name"><input type="text" defaultValue={part.name} key={part.id + part.name} onBlur={(e) => e.target.value !== part.name && apply(() => api.updatePart(part.id, { name: e.target.value }))} /></Field>
                <div className="row">
                  <div className="grow"><Field label="Material">
                    <Select value={part.material} onChange={(m) => apply(() => api.updatePart(part.id, { material: m, color: meta.materials[m]?.color }))} options={Object.entries(meta.materials).map(([k, v]) => ({ value: k, label: v.label }))} />
                  </Field></div>
                  <Field label="Colour"><input type="color" value={part.color} onChange={(e) => apply(() => api.updatePart(part.id, { color: e.target.value }))} style={{ width: 44, height: 28, padding: 0, border: 0, background: "none" }} /></Field>
                </div>
              </div>
            </Section>

            <ParametersEditor params={part.parameters} onChange={setParameters} />

            {feature && meta.features[feature.type] && (
              <Section title={`${meta.features[feature.type].label} — parameters`} actions={<button className="btn ghost sm" onClick={() => setSelFeature(null)}>✕</button>}>
                <div className="col">
                  <Field label="Feature name"><input type="text" defaultValue={feature.name} key={feature.id + feature.name} onBlur={(e) => e.target.value !== feature.name && apply(() => api.updateFeature(part.id, feature.id, { name: e.target.value }))} /></Field>
                  <ParamForm
                    defs={meta.features[feature.type].params}
                    values={feature.params}
                    onChange={patchParam}
                    ctx={{ sketches, features: part.features.filter((f) => f.id !== feature.id), picked: pickedFace != null ? pickRefs : undefined }}
                  />
                  {status(feature.id)?.error && <div className="notice err">{status(feature.id)!.error}</div>}
                  {(status(feature.id)?.warnings ?? []).map((w, i) => <div key={i} className="notice warn">{w}</div>)}
                  <div className="muted small">Numeric fields accept expressions: <span className="mono hl">=width/2 + 3</span></div>
                </div>
              </Section>
            )}

            {props && (
              <Section title="Mass properties">
                <div className="kv">
                  <span>Volume</span><span>{fmt(props.volume_mm3 / 1000)} cm³</span>
                  <span>Surface area</span><span>{fmt(props.area_mm2 / 100)} cm²</span>
                  <span>Mass</span><span>{fmt(props.mass_kg * 1000)} g</span>
                  <span>Centre of mass</span><span>{props.center_of_mass.map((x) => x.toFixed(1)).join(", ")}</span>
                  <span>Bounding box</span><span>{props.bbox_size.map((x) => x.toFixed(1)).join(" × ")}</span>
                  <span>Faces / edges</span><span>{props.faces} / {props.edges}</span>
                </div>
              </Section>
            )}

            <Section title="Export">
              <div className="row wrap">
                {["step", "stl", "brep"].map((f) => <a key={f} className="btn sm" href={url(`/api/parts/${part.id}/export?format=${f}`)} download>{f.toUpperCase()}</a>)}
              </div>
            </Section>
          </>
        )}
      </div>
    </div>
  );
}

/** Closest B-rep edge to a picked point, if it is close relative to the model size. */
function nearestEdge(mesh: MeshData, p: number[]): { index: number; type: string } | null {
  let best: { index: number; type: string } | null = null, bd = Infinity, span = 0;
  for (const e of mesh.edges) {
    const q = e.points;
    for (let i = 0; i + 5 < q.length; i += 3) {
      const ax = q[i], ay = q[i + 1], az = q[i + 2], dx = q[i + 3] - ax, dy = q[i + 4] - ay, dz = q[i + 5] - az;
      const L2 = dx * dx + dy * dy + dz * dz || 1;
      const t = Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy + (p[2] - az) * dz) / L2));
      const d = Math.hypot(ax + t * dx - p[0], ay + t * dy - p[1], az + t * dz - p[2]);
      if (d < bd) { bd = d; best = { index: e.index, type: e.type }; }
      span = Math.max(span, Math.abs(ax), Math.abs(ay), Math.abs(az));
    }
  }
  return best && bd < Math.max(span * 0.08, 1) ? best : null;
}

function ParametersEditor({ params, onChange }: { params: Record<string, Json>; onChange: (p: Record<string, Json>) => void }) {
  const [name, setName] = useState("");
  const entries = Object.entries(params ?? {});
  return (
    <Section title="Named parameters" actions={<span className="muted small" style={{ textTransform: "none", letterSpacing: 0 }}>use as =name</span>}>
      <table className="t">
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k}>
              <td className="mono">{k}</td>
              <td><NumField value={v} allowExpr onChange={(x) => onChange({ ...params, [k]: x ?? 0 })} /></td>
              <td><button className="btn ghost sm" onClick={() => { const c = { ...params }; delete c[k]; onChange(c); }}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 6 }}>
        <input type="text" placeholder="new parameter, e.g. width" value={name} onChange={(e) => setName(e.target.value.replace(/[^A-Za-z0-9_]/g, ""))} />
        <button className="btn sm" disabled={!name || name in params} onClick={() => { onChange({ ...params, [name]: 10 }); setName(""); }}>Add</button>
      </div>
    </Section>
  );
}
