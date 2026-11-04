import { useCallback, useEffect, useRef, useState } from "react";
import { api, url } from "../api";
import { SelectorInput, describeSelector } from "../components/ParamForm";
import { Viewport } from "../components/Viewport";
import { Field, NumField, Section, Select, VecField, fmt } from "../components/ui";
import { useApp } from "../store";
import type { Assembly, Component, Json, MeshData } from "../types";

interface AsmMesh { components: (Component & { color: string; matrix: number[]; has_geometry: boolean })[]; meshes: Record<string, MeshData>; assembly: Assembly }

export default function AssemblyWorkspace() {
  const { meta, project, parts, notify, run } = useApp();
  const [list, setList] = useState<Assembly[]>([]);
  const [aid, setAid] = useState<number | null>(null);
  const [data, setData] = useState<AsmMesh | null>(null);
  const [selComp, setSelComp] = useState<number | null>(null);
  const [mateStatus, setMateStatus] = useState<Json>(null);
  const [solveInfo, setSolveInfo] = useState<{ status: string; dof: number } | null>(null);
  const [interf, setInterf] = useState<Json>(null);
  const [bom, setBom] = useState<Json>(null);
  const [pickFor, setPickFor] = useState<"a" | "b" | null>(null);
  const [mate, setMate] = useState<{ type: string; comp_a: number | null; comp_b: number | null; params: Record<string, Json> }>({ type: "flush", comp_a: null, comp_b: null, params: {} });
  const [explode, setExplode] = useState(0);
  const pickRef = useRef(pickFor);
  pickRef.current = pickFor;
  const mateRef = useRef(mate);
  mateRef.current = mate;
  const seq = useRef(0);

  const loadList = useCallback(async () => {
    if (!project) return [];
    const l = await api.assemblies(project.id);
    setList(l);
    return l;
  }, [project]);

  useEffect(() => {
    setAid(null); setData(null);
    loadList().then((l) => setAid(l[0]?.id ?? null)).catch((e) => notify(String(e)));
  }, [loadList, notify]);

  const loadMesh = useCallback(async (id: number) => {
    const my = ++seq.current;
    try {
      const r = (await api.assemblyMesh(id)) as AsmMesh;
      if (my === seq.current) setData(r);
    } catch (e) { notify(String(e)); }
  }, [notify]);

  useEffect(() => {
    setInterf(null); setBom(null); setMateStatus(null); setSolveInfo(null); setSelComp(null);
    if (aid != null) loadMesh(aid); else setData(null);
  }, [aid, loadMesh]);

  if (!project || !meta) return <div className="empty"><div><h3>No project</h3></div></div>;
  const asm = data?.assembly ?? list.find((a) => a.id === aid) ?? null;
  const comps = asm?.components ?? [];
  const comp = comps.find((c) => c.id === selComp) ?? null;
  const reload = async () => { await loadList(); if (aid != null) await loadMesh(aid); };

  const mateDefs = meta.mate_types.find((m) => m.type === mate.type);
  const statusOf = (id: number) => {
    if (!mateStatus) return null;
    if (Array.isArray(mateStatus)) return mateStatus.find((s: Json) => s.id === id || s.mate_id === id) ?? null;
    return mateStatus[id] ?? mateStatus[String(id)] ?? null;
  };

  return (
    <div className="workspace">
      <div className="side left">
        <Section title="Assemblies" actions={
          <button className="btn sm" onClick={async () => {
            const a = await run("creating…", () => api.createAssembly(project.id, `Assembly ${list.length + 1}`));
            if (a) { await loadList(); setAid(a.id); }
          }}>＋ New</button>
        } pad={false}>
          <div className="list">
            {list.map((a) => (
              <div key={a.id} className={`item ${a.id === aid ? "sel" : ""}`} onClick={() => setAid(a.id)}>
                <span className="name">{a.name}</span><span className="pill">{a.components.length} parts</span>
                <button className="btn ghost sm" onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete assembly "${a.name}"?`)) return;
                  await run("deleting…", () => api.deleteAssembly(a.id));
                  const l = await loadList();
                  if (a.id === aid) setAid(l[0]?.id ?? null);
                }}>✕</button>
              </div>
            ))}
            {!list.length && <div className="item muted">No assemblies yet</div>}
          </div>
        </Section>

        {asm && (
          <>
            <Section title="Components" actions={
              <select style={{ width: 130 }} value="" onChange={async (e) => {
                const pid = Number(e.target.value);
                if (!pid) return;
                const n = comps.length;
                await run("adding…", () => api.addComponent(asm.id, pid, [n * 60, 0, 0]));
                await reload();
              }}>
                <option value="">＋ Insert part…</option>
                {parts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            } pad={false}>
              <div className="list">
                {comps.map((c) => (
                  <div key={c.id} className={`item ${c.id === selComp ? "sel" : ""}`} onClick={() => setSelComp(c.id)}>
                    <span className="name">{c.name}</span>
                    {c.fixed && <span className="pill warn">fixed</span>}
                    <button className="btn ghost sm" onClick={async (e) => { e.stopPropagation(); await run("deleting…", () => api.deleteComponent(c.id)); setSelComp(null); await reload(); }}>✕</button>
                  </div>
                ))}
                {!comps.length && <div className="item muted">Insert parts to begin</div>}
              </div>
            </Section>

            <Section title="Mates" pad={false}>
              <div className="list">
                {asm.mates.map((m) => {
                  const st = statusOf(m.id);
                  const bad = st != null && st !== "ok";
                  const nm = (id: number) => comps.find((c) => c.id === id)?.name ?? `#${id}`;
                  const val = m.params?.value != null ? ` = ${m.params.value}${m.type === "angle" ? "°" : " mm"}` : "";
                  return (
                    <div key={m.id} className="item" title={[st, describeSelector(m.params?.a_face ?? ""), describeSelector(m.params?.b_face ?? "")].filter(Boolean).join(" · ")}>
                      <span className={`dot ${bad ? "bad" : st ? "" : "off"}`} />
                      <span className="name">{m.type}{val}: {nm(m.comp_a)} ↔ {nm(m.comp_b)}</span>
                      <button className="btn ghost sm" onClick={async () => { await run("deleting…", () => api.deleteMate(m.id)); await reload(); }}>✕</button>
                    </div>
                  );
                })}
                {!asm.mates.length && <div className="item muted">No mates</div>}
              </div>
            </Section>
          </>
        )}
      </div>

      <div className="center">
        <Viewport
          deps={[data, selComp, explode]}
          fitOn={data ? aid : null}
          setup={(v) => {
            v.clear();
            if (data) {
              const centre = data.components.length ? data.components.reduce((s, c) => [s[0] + c.position[0], s[1] + c.position[1], s[2] + c.position[2]], [0, 0, 0]).map((x) => x / data.components.length) : [0, 0, 0];
              for (const c of data.components) {
                const m = data.meshes[String(c.part_id)];
                if (!m || !c.has_geometry) continue;
                const mat = [...c.matrix];
                if (explode) { for (let k = 0; k < 3; k++) mat[k * 4 + 3] += (c.position[k] - centre[k]) * explode; }
                v.setSolid(`c${c.id}`, m, { color: c.id === selComp ? "#f5a623" : c.color, matrix: mat });
              }
              if (interf?.interferences) interf.interferences.forEach((h: Json, i: number) => v.setMarker(`i${i}`, h.center, 3, 0xef5b5b));
            }
            v.onPick((h) => {
              if (!h) return;
              const cid = Number(h.key.replace("c", ""));
              if (pickRef.current && h.face) {
                const side = pickRef.current;
                const idx = h.face.index;
                const partId = data?.components.find((c) => c.id === cid)?.part_id;
                setPickFor(null);
                // store a persistent reference so the mate survives edits to the part
                const ref = partId != null ? api.partReference(partId, "face", [idx]).then((r) => r.selector).catch(() => `idx:${idx}`) : Promise.resolve(`idx:${idx}`);
                ref.then((sel) => setMate((m) => ({ ...m, [side === "a" ? "comp_a" : "comp_b"]: cid, params: { ...m.params, [`${side}_face`]: sel } })));
              } else setSelComp(cid);
            });
          }}
        >
          <div className="overlay tl">
            <div className="hud">
              <b>{asm?.name ?? "No assembly"}</b>
              {pickFor && <div className="hl small">Click a face on component {pickFor.toUpperCase()}…</div>}
              {interf && <div className="small" style={{ color: interf.count ? "var(--danger)" : "var(--ok)" }}>{interf.count ? `${interf.count} interference(s)` : "No interference"}</div>}
            </div>
          </div>
          <div className="overlay bl">
            <div className="hud row"><span className="small muted">Explode</span><input type="range" min={0} max={1.5} step={0.05} value={explode} onChange={(e) => setExplode(Number(e.target.value))} style={{ width: 120 }} /></div>
          </div>
        </Viewport>
      </div>

      <div className="side right">
        {asm && (
          <>
            <Section title="Solve & analyse">
              <div className="row wrap">
                <button className="btn primary sm" onClick={async () => {
                  const r = await run("solving mates…", () => api.solveAssembly(asm.id));
                  if (r) {
                    setMateStatus(r.mate_status); setSolveInfo({ status: r.status, dof: r.dof }); await loadMesh(asm.id);
                    notify(r.status === "conflict" ? "Some mates cannot be satisfied together" : "Mates solved", r.status === "conflict" ? "err" : "ok");
                  }
                }}>Solve mates</button>
                <button className="btn sm" onClick={async () => { const r = await run("checking…", () => api.interference(asm.id)); if (r) setInterf(r); }}>Interference</button>
                <button className="btn sm" onClick={async () => { const r = await run("BOM…", () => api.bom(asm.id)); if (r) setBom(r); }}>BOM</button>
                <a className="btn sm" href={url(`/api/assemblies/${asm.id}/export?format=step`)} download>STEP</a>
              </div>
              {solveInfo && (
                <div style={{ marginTop: 8 }}>
                  <span className={`pill ${solveInfo.status === "conflict" ? "bad" : solveInfo.status === "fully_constrained" ? "ok" : "warn"}`}>
                    {solveInfo.status.replace("_", " ")} · {solveInfo.dof} DOF free
                  </span>
                  <div className="muted small" style={{ marginTop: 4 }}>All mates are solved together; free DOF stay where they are.</div>
                </div>
              )}
              {interf?.interferences?.length > 0 && (
                <table className="t" style={{ marginTop: 8 }}>
                  <thead><tr><th>Pair</th><th className="num">Vol mm³</th></tr></thead>
                  <tbody>{interf.interferences.map((h: Json, i: number) => <tr key={i}><td>{h.name_a} × {h.name_b}</td><td className="num">{fmt(h.volume_mm3)}</td></tr>)}</tbody>
                </table>
              )}
            </Section>

            {comp && (
              <Section title={`Component: ${comp.name}`}>
                <div className="col">
                  <Field label="Position (mm)"><VecField n={3} value={comp.position} onChange={async (p) => { await api.updateComponent(comp.id, { position: p }); await reload(); }} /></Field>
                  <Field label="Rotation (° about X, Y, Z)"><VecField n={3} value={comp.rotation} onChange={async (r) => { await api.updateComponent(comp.id, { rotation: r }); await reload(); }} /></Field>
                  <label className="check"><input type="checkbox" checked={comp.fixed} onChange={async (e) => { await api.updateComponent(comp.id, { fixed: e.target.checked }); await reload(); }} />Fixed (ground)</label>
                </div>
              </Section>
            )}

            <Section title="Add mate">
              <div className="col">
                <Select value={mate.type} onChange={(t) => setMate({ type: t, comp_a: mate.comp_a, comp_b: mate.comp_b, params: {} })} options={meta.mate_types.map((m) => ({ value: m.type, label: m.type }))} />
                {(["a", "b"] as const).map((s) => (
                  <div key={s} className="row">
                    <div className="grow">
                      <Select value={mate[s === "a" ? "comp_a" : "comp_b"] ?? ""} onChange={(v) => setMate({ ...mate, [s === "a" ? "comp_a" : "comp_b"]: Number(v) })}
                        options={[{ value: "", label: `Component ${s.toUpperCase()}${s === "a" ? " (stays)" : " (moves)"}` }, ...comps.map((c) => ({ value: c.id, label: c.name }))]} />
                    </div>
                    {mateDefs?.params.includes(`${s}_face`) && <button className={`btn sm ${pickFor === s ? "active" : ""}`} onClick={() => setPickFor(pickFor === s ? null : s)}>pick face</button>}
                  </div>
                ))}
                {(mateDefs?.params ?? []).map((p) => {
                  const v = mate.params[p];
                  const set = (x: Json) => setMate((m) => ({ ...m, params: { ...m.params, [p]: x } }));
                  if (p.endsWith("_face")) return <Field key={p} label={`${p.startsWith("a") ? "A" : "B"} face`}><SelectorInput value={v ?? ""} onChange={set} /></Field>;
                  if (p === "value") return <Field key={p} label={mate.type === "angle" ? "Angle (°)" : "Distance (mm)"}><NumField value={v ?? (mate.type === "angle" ? 90 : 0)} onChange={(x) => set(x ?? 0)} /></Field>;
                  if (p.endsWith("_point") || p === "offset" && mate.type === "point") return <Field key={p} label={p}><VecField n={3} value={v ?? [0, 0, 0]} onChange={set} /></Field>;
                  if (["same_direction", "align_center", "flip"].includes(p)) return <label key={p} className="check"><input type="checkbox" checked={!!v} onChange={(e) => set(e.target.checked)} />{p.replace(/_/g, " ")}</label>;
                  return <Field key={p} label={p}><NumField value={v ?? 0} onChange={(x) => set(x ?? 0)} /></Field>;
                })}
                <button className="btn primary sm" disabled={mate.comp_a == null || mate.comp_b == null || mate.comp_a === mate.comp_b} onClick={async () => {
                  await run("adding mate…", () => api.addMate(asm.id, { type: mate.type, comp_a: mate.comp_a!, comp_b: mate.comp_b!, params: mate.params }));
                  setMate({ ...mate, params: {} });
                  await reload();
                }}>Add mate</button>
                <div className="muted small">Faces: viewport picks (stored as persistent references), cadquery selectors (<span className="mono">&gt;Z</span>, <span className="mono">%CYLINDER</span>) or <span className="mono">near:x,y,z</span> in part coordinates. Planes, cylinders, cones and spheres can be mated.</div>
              </div>
            </Section>

            {bom && (
              <Section title="Bill of materials">
                <table className="t">
                  <thead><tr><th>#</th><th>Part</th><th className="num">Qty</th><th className="num">Mass g</th></tr></thead>
                  <tbody>
                    {bom.rows.map((r: Json) => <tr key={r.part_id}><td>{r.item}</td><td>{r.name}<div className="muted small">{r.material}</div></td><td className="num">{r.quantity}</td><td className="num">{fmt(r.total_mass_kg * 1000, 1)}</td></tr>)}
                  </tbody>
                </table>
                <div className="kv" style={{ marginTop: 6 }}><span>Total parts</span><span>{bom.total_parts}</span><span>Total mass</span><span>{fmt(bom.total_mass_kg * 1000, 1)} g</span></div>
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
