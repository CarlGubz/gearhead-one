import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { SelectorInput, describeSelector } from "../components/ParamForm";
import { Viewport } from "../components/Viewport";
import { Field, NumField, Section, Select, VecField, fmt } from "../components/ui";
import { useApp } from "../store";
import type { Assembly, Component, Json, MeshData, SimJob } from "../types";

type BCKind = "fixed" | "forces" | "pressures" | "fixed_temps" | "heat_flux" | "convection";
type BCDef = { kind: BCKind; label: string; color: number; fields: string[] };
const STRUCT: BCDef[] = [
  { kind: "fixed", label: "Fixed support", color: 0x0b63c5, fields: ["dofs"] },
  { kind: "forces", label: "Force (N)", color: 0xc8361b, fields: ["vector"] },
  { kind: "pressures", label: "Pressure (MPa)", color: 0xee8426, fields: ["value"] },
];
const THERMAL: BCDef[] = [
  { kind: "fixed_temps", label: "Fixed temperature (°C)", color: 0x0b63c5, fields: ["temperature"] },
  { kind: "heat_flux", label: "Heat flux (W/m²)", color: 0xc8361b, fields: ["value"] },
  { kind: "convection", label: "Convection", color: 0xee8426, fields: ["h", "ambient"] },
];
const SUPPORT_ONLY: BCDef[] = [STRUCT[0]];
const BC_BY_KIND: Record<string, BCDef[]> = {
  static: STRUCT, thermal: THERMAL, modal: SUPPORT_ONLY,
  ccx_static: STRUCT, ccx_buckling: STRUCT, ccx_frequency: SUPPORT_ONLY, ccx_thermal_transient: THERMAL,
  ccx_dynamic: STRUCT, ccx_contact: STRUCT,
};
const KIND_LABEL: Record<string, string> = {
  static: "Linear static (stress)", thermal: "Steady-state thermal", modal: "Modal (natural frequencies)",
  ccx_static: "Nonlinear static — plasticity / large deformation", ccx_buckling: "Buckling (load factors)",
  ccx_frequency: "Natural frequencies (CalculiX)", ccx_thermal_transient: "Transient thermal", ccx_dynamic: "Transient dynamic",
  ccx_contact: "Contact between components",
};
const MODE_KINDS = new Set(["modal", "ccx_buckling", "ccx_frequency"]);
const THERMAL_KINDS = new Set(["thermal", "ccx_thermal_transient"]);
const DEFAULTS: Record<string, Json> = { vector: [0, 0, -100], value: 1, temperature: 100, h: 25, ambient: 20, dofs: [true, true, true] };
const FIELD_LABEL: Record<string, string> = {
  von_mises: "von Mises stress (MPa)", displacement: "Displacement (mm)", temperature: "Temperature (°C)",
  plastic_strain: "Equivalent plastic strain", contact_pressure: "Contact pressure (MPa)",
};
const defaultField = (k: string) => (THERMAL_KINDS.has(k) ? "temperature" : MODE_KINDS.has(k) ? "displacement" : "von_mises");
type Pick = { face: number; component?: number; part?: number };
interface AsmMesh { components: (Component & { color: string; matrix: number[]; has_geometry: boolean })[]; meshes: Record<string, MeshData>; assembly: Assembly }

export default function SimulateWorkspace() {
  const { meta, project, parts, partId, setPartId, notify, run } = useApp();
  const part = parts.find((p) => p.id === partId) ?? null;
  const [source, setSource] = useState<"part" | "assembly">("part");
  const [assemblies, setAssemblies] = useState<Assembly[]>([]);
  const [asmId, setAsmId] = useState<number | null>(null);
  const [asmMesh, setAsmMesh] = useState<AsmMesh | null>(null);
  const [mesh, setMesh] = useState<MeshData | null>(null);
  const [kind, setKind] = useState("static");
  const [bcs, setBcs] = useState<Record<string, Json[]>>({});
  const [opts, setOpts] = useState<Record<string, Json>>({ mesh_size: 0, modes: 6, gravity: false, nlgeom: false, plastic: false, tangent_modulus: 0, increments: 10,
    initial_temperature: 20, time_period: 60, time_step: 2, dyn_period: 0.01, dyn_step: 0.0001, load_profile: "step", friction: 0.2, contact_type: "contact" });
  const [picked, setPicked] = useState<Pick | null>(null);
  const [pickRef, setPickRef] = useState<string | undefined>();
  const [jobs, setJobs] = useState<SimJob[]>([]);
  const [job, setJob] = useState<SimJob | null>(null);
  const [field, setField] = useState("von_mises");
  const [mode, setMode] = useState(0);
  const [scaleMul, setScaleMul] = useState(1);
  const [wire, setWire] = useState(false);
  const poll = useRef<number>();
  const ccx = meta?.calculix;
  const isAsm = source === "assembly";
  const opt = (k: string) => opts[k];
  const setOpt = (k: string, v: Json) => setOpts((o) => ({ ...o, [k]: v }));

  const loadJobs = useCallback(() => {
    if (isAsm) { if (asmId != null) api.asmSims(asmId).then(setJobs).catch(() => {}); else setJobs([]); }
    else if (partId != null) api.sims(partId).then(setJobs).catch(() => {});
    else setJobs([]);
  }, [isAsm, asmId, partId]);

  useEffect(() => {
    setMesh(null); setJob(null); setPicked(null); setBcs({});
    if (!isAsm && partId != null) api.partMesh(partId).then((r) => setMesh(r.mesh)).catch((e) => notify(String(e)));
    loadJobs();
  }, [partId, isAsm, notify, loadJobs]);

  useEffect(() => {
    if (!isAsm || !project) return;
    api.assemblies(project.id).then((l) => { setAssemblies(l); setAsmId((cur) => cur ?? l[0]?.id ?? null); }).catch(() => {});
    setKind("ccx_contact");
  }, [isAsm, project]);

  useEffect(() => {
    setAsmMesh(null); setJob(null); setPicked(null); setBcs({});
    if (isAsm && asmId != null) api.assemblyMesh(asmId).then(setAsmMesh).catch((e) => notify(String(e)));
  }, [isAsm, asmId, notify]);

  // persistent reference for the picked face (survives edits to the part)
  useEffect(() => {
    setPickRef(undefined);
    if (!picked?.part && picked?.part !== 0) return;
    let live = true;
    api.partReference(picked.part, "face", [picked.face]).then((r) => live && setPickRef(r.selector)).catch(() => live && setPickRef(`idx:${picked.face}`));
    return () => { live = false; };
  }, [picked]);

  useEffect(() => () => window.clearInterval(poll.current), []);

  const watch = useCallback((id: number) => {
    window.clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      try {
        const j = await api.sim(id);
        setJob(j);
        if (j.status === "done" || j.status === "error") {
          window.clearInterval(poll.current);
          if (j.status === "error") notify(j.error);
          else { notify("Simulation finished", "ok"); setField(defaultField(j.kind)); setMode(0); }
          loadJobs();
        }
      } catch (e) { window.clearInterval(poll.current); notify(String(e)); }
    }, 1000);
  }, [notify, loadJobs]);

  if (!project || !meta) return <div className="empty"><div><h3>No project</h3></div></div>;

  const defs = BC_BY_KIND[kind] ?? STRUCT;
  const isCcx = kind.startsWith("ccx_");
  const ccxMissing = isCcx && !ccx?.available;
  const buildConfig = () => {
    const cfg: Json = {};
    if (opt("mesh_size") > 0) cfg.mesh_size = opt("mesh_size");
    defs.forEach((d) => { if (bcs[d.kind]?.length) cfg[d.kind] = bcs[d.kind].map(({ _pick, ...r }) => r); });
    if (MODE_KINDS.has(kind)) cfg.modes = opt("modes");
    if (["static", "ccx_static", "ccx_contact", "ccx_buckling"].includes(kind) && opt("gravity")) cfg.gravity = { enabled: true };
    if (kind === "ccx_static" || kind === "ccx_contact") {
      cfg.nlgeom = opt("nlgeom");
      cfg.increments = opt("increments");
      if (opt("plastic")) cfg.plasticity = { enabled: true, ...(opt("tangent_modulus") > 0 ? { tangent_modulus: opt("tangent_modulus") } : {}) };
    }
    if (kind === "ccx_thermal_transient") Object.assign(cfg, { initial_temperature: opt("initial_temperature"), time_period: opt("time_period"), time_step: opt("time_step") });
    if (kind === "ccx_dynamic") Object.assign(cfg, { time_period: opt("dyn_period"), time_step: opt("dyn_step"), load_profile: opt("load_profile") });
    if (kind === "ccx_contact") Object.assign(cfg, { friction: opt("friction"), contact_type: opt("contact_type") });
    return cfg;
  };

  const start = async () => {
    const j = isAsm
      ? asmId != null ? await run("submitting…", () => api.startAsmSim(asmId, kind, buildConfig())) : null
      : partId != null ? await run("submitting…", () => api.startSim(partId, kind, buildConfig())) : null;
    if (j) { setJob(j); watch(j.id); }
  };

  const pickRow = (): Json => (picked ? { face: pickRef ?? `idx:${picked.face}`, _pick: picked, ...(picked.component != null ? { component: picked.component } : {}) } : { face: ">Z" });
  const addBc = (k: BCKind, fields: string[]) => {
    const row: Json = pickRow();
    if (isAsm && row.component == null) row.component = asmMesh?.components[0]?.id;
    fields.forEach((f) => (row[f] = structuredClone(DEFAULTS[f])));
    setBcs({ ...bcs, [k]: [...(bcs[k] ?? []), row] });
  };
  const patchBc = (k: BCKind, i: number, patch: Json) => setBcs({ ...bcs, [k]: bcs[k].map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const delBc = (k: BCKind, i: number) => setBcs({ ...bcs, [k]: bcs[k].filter((_, j) => j !== i) });

  // result surface for the current mode / field
  const result = job?.status === "done" ? (job.result as Json) : null;
  const rKind: string = result?.kind ?? kind;
  const surface: Json = result ? (MODE_KINDS.has(rKind) ? result.modes?.[mode]?.surface : result.surface) : null;
  const fieldKey = surface?.fields?.[field] ? field : Object.keys(surface?.fields ?? {})[0];
  const fld = surface?.fields?.[fieldKey];
  const baseScale = (MODE_KINDS.has(rKind) ? 1 : surface?.suggested_deformation_scale) ?? 1;
  const kindOptions = isAsm ? ["ccx_contact"] : meta.simulation_kinds;
  const compName = (id: number) => asmMesh?.components.find((c) => c.id === id)?.name ?? `#${id}`;

  return (
    <div className="workspace wide-right">
      <div className="side left">
        <Section title="Analyse">
          <div className="col">
            <div className="row">
              <button className={`btn sm grow ${!isAsm ? "active" : ""}`} onClick={() => { setSource("part"); setKind("static"); }}>Part</button>
              <button className={`btn sm grow ${isAsm ? "active" : ""}`} onClick={() => setSource("assembly")}>Assembly (contact)</button>
            </div>
            {isAsm
              ? <Select value={asmId ?? ""} onChange={(v) => setAsmId(Number(v))} options={assemblies.length ? assemblies.map((a) => ({ value: a.id, label: a.name })) : [{ value: "", label: "No assemblies" }]} />
              : <Select value={partId ?? ""} onChange={(v) => setPartId(Number(v))} options={parts.map((p) => ({ value: p.id, label: p.name }))} />}
          </div>
        </Section>
        {!isAsm && part && <Section title="Material">
          {meta.materials[part.material] && (() => { const m = meta.materials[part.material]; return (
            <div className="kv"><span>Material</span><span>{m.label}</span><span>E</span><span>{m.E} GPa</span><span>ν</span><span>{m.nu}</span><span>Yield</span><span>{m.yield_strength} MPa</span><span>Density</span><span>{m.density} kg/m³</span></div>
          ); })()}
          <div className="muted small" style={{ marginTop: 6 }}>Change the material in the Model workspace.</div>
        </Section>}
        <Section title="Solvers">
          <div className="kv">
            <span>Native</span><span>linear static · thermal · modal</span>
            <span>CalculiX</span><span style={{ color: ccx?.available ? "var(--ok)" : "var(--amber)" }}>{ccx?.available ? "available" : "not installed"}</span>
          </div>
          {!ccx?.available && ccx?.hint && <div className="muted small" style={{ marginTop: 6 }}>{ccx.hint}</div>}
        </Section>
        <Section title="History" pad={false}>
          <div className="list">
            {jobs.map((j) => (
              <div key={j.id} className={`item ${job?.id === j.id ? "sel" : ""}`} onClick={async () => { const full = await api.sim(j.id); setJob(full); setKind(full.kind); if (full.status === "done") setField(defaultField(full.kind)); else if (full.status !== "error") watch(full.id); }}>
                <span className={`dot ${j.status === "error" ? "bad" : j.status === "done" ? "" : "off"}`} />
                <span className="name">#{j.id} {KIND_LABEL[j.kind]?.split(" —")[0] ?? j.kind}</span><span className="pill">{j.status}</span>
              </div>
            ))}
            {!jobs.length && <div className="item muted">No runs yet</div>}
          </div>
        </Section>
      </div>

      <div className="center">
        <Viewport
          deps={[mesh, asmMesh, picked, job, field, mode, wire, scaleMul, bcs, kind]}
          fitOn={(isAsm ? asmMesh : mesh) ? `${source}-${isAsm ? asmId : partId}-${surface ? job?.id : 0}` : null}
          setup={(v) => {
            v.clear();
            v.setAnimation(null);
            v.setWireframe(wire);
            if (surface && fld) {
              const key = "res";
              v.setField(key, { positions: surface.positions, indices: surface.indices, values: fld.values, min: fld.min, max: fld.max, displacements: surface.displacements, scale: surface.displacements ? baseScale * scaleMul * (MODE_KINDS.has(rKind) ? 0.6 : 1) : 0 });
              if (MODE_KINDS.has(rKind)) v.setAnimation((t) => v.applyDeformation(key, Math.sin(t / 350) * scaleMul));
            } else if (isAsm && asmMesh) {
              for (const c of asmMesh.components) {
                const m = asmMesh.meshes[String(c.part_id)];
                if (m && c.has_geometry) v.setSolid(`c${c.id}`, m, { color: c.color, matrix: c.matrix });
              }
              defs.forEach((d) => (bcs[d.kind] ?? []).forEach((b: Json) => b._pick?.component != null && v.highlightFaces(`c${b._pick.component}`, [b._pick.face], d.color)));
              if (picked?.component != null) v.highlightFaces(`c${picked.component}`, [picked.face]);
            } else if (mesh) {
              v.setSolid("part", mesh, { color: part?.color });
              defs.forEach((d) => {
                const idx = (bcs[d.kind] ?? []).map((b: Json) => b._pick?.face ?? Number(/^idx:(\d+)$/.exec(String(b.face))?.[1] ?? NaN)).filter((x: number) => Number.isFinite(x));
                if (idx.length) v.highlightFaces("part", idx, d.color);
              });
              if (picked != null && !Object.values(bcs).flat().length) v.highlightFaces("part", [picked.face]);
            }
            v.onPick((h) => {
              if (!h?.face || surface) return;
              if (isAsm) {
                const cid = Number(h.key.replace("c", ""));
                setPicked({ face: h.face.index, component: cid, part: asmMesh?.components.find((c) => c.id === cid)?.part_id });
              } else if (partId != null) setPicked({ face: h.face.index, part: partId });
            });
          }}
        >
          <div className="overlay tl">
            <div className="hud">
              <b>{isAsm ? assemblies.find((a) => a.id === asmId)?.name ?? "—" : part?.name ?? "—"}</b> <span className="pill">{KIND_LABEL[kind]?.split(" —")[0] ?? kind}</span>
              {job && <span className={`pill ${job.status === "done" ? "ok" : job.status === "error" ? "bad" : "warn"}`}>{job.status}</span>}
              {!surface && <div className="small muted">Click a face to pick it, then add it to a boundary condition.</div>}
              {picked != null && !surface && <div className="small hl">picked {pickRef ? describeSelector(pickRef) : `face ${picked.face}`}{picked.component != null ? ` on ${compName(picked.component)}` : ""}</div>}
              {job && (job.status === "queued" || job.status === "running") && <div className="small">⏳ meshing & solving{isCcx ? " with CalculiX" : ""}…</div>}
            </div>
          </div>
          {surface && fld && (
            <div className="overlay br">
              <div className="hud legend">
                <b className="small">{FIELD_LABEL[fieldKey] ?? fieldKey}</b>
                <div className="bar" />
                <div className="ticks"><span>{fmt(fld.min, 3)}</span><span>{fmt((fld.min + fld.max) / 2, 3)}</span><span>{fmt(fld.max, 3)}</span></div>
              </div>
            </div>
          )}
          <div className="overlay bl">
            {surface && <button className="btn sm" onClick={() => setJob(null)}>← back to setup</button>}
          </div>
        </Viewport>
      </div>

      <div className="side right">
        <Section title="Analysis">
          <div className="col">
            <Select value={kind} onChange={(k) => { setKind(k); setJob(null); }} options={kindOptions.map((k) => ({ value: k, label: (KIND_LABEL[k] ?? k) + (k.startsWith("ccx_") && !ccx?.available ? " (needs CalculiX)" : "") }))} />
            {isCcx && <div className="muted small">Solved by CalculiX on quadratic tetrahedra (C3D10).</div>}
            <Field label="Mesh element size (mm, 0 = automatic)"><NumField allowExpr={false} value={opt("mesh_size")} onChange={(v) => setOpt("mesh_size", v ?? 0)} /></Field>
            {MODE_KINDS.has(kind) && <Field label={kind === "ccx_buckling" ? "Number of buckling modes" : "Number of modes"}><NumField allowExpr={false} value={opt("modes")} onChange={(v) => setOpt("modes", Math.max(1, Math.round(v ?? 6)))} /></Field>}
            {["static", "ccx_static", "ccx_contact", "ccx_buckling"].includes(kind) && <label className="check"><input type="checkbox" checked={opt("gravity")} onChange={(e) => setOpt("gravity", e.target.checked)} />Gravity (−Z)</label>}
            {(kind === "ccx_static" || kind === "ccx_contact") && <>
              <label className="check"><input type="checkbox" checked={opt("nlgeom")} onChange={(e) => setOpt("nlgeom", e.target.checked)} />Large deformation (NLGEOM)</label>
              <label className="check"><input type="checkbox" checked={opt("plastic")} onChange={(e) => setOpt("plastic", e.target.checked)} />Plasticity (bilinear, yield from material)</label>
              {opt("plastic") && <Field label="Tangent modulus after yield (MPa, 0 = 1% of E)"><NumField allowExpr={false} value={opt("tangent_modulus")} onChange={(v) => setOpt("tangent_modulus", v ?? 0)} /></Field>}
              <Field label="Load increments"><NumField allowExpr={false} value={opt("increments")} onChange={(v) => setOpt("increments", Math.max(1, Math.round(v ?? 10)))} /></Field>
            </>}
            {kind === "ccx_contact" && <>
              <Field label="Contact"><Select value={opt("contact_type")} onChange={(v) => setOpt("contact_type", v)} options={[{ value: "contact", label: "Frictional contact (can separate / slide)" }, { value: "bonded", label: "Bonded (glued)" }]} /></Field>
              {opt("contact_type") === "contact" && <Field label="Friction coefficient"><NumField allowExpr={false} value={opt("friction")} onChange={(v) => setOpt("friction", v ?? 0)} /></Field>}
              <div className="muted small">Touching planar / cylindrical faces between components are paired automatically — mate the components first. Every component needs enough support (or contact) not to float away.</div>
            </>}
            {kind === "ccx_thermal_transient" && <>
              <Field label="Initial temperature (°C)"><NumField allowExpr={false} value={opt("initial_temperature")} onChange={(v) => setOpt("initial_temperature", v ?? 20)} /></Field>
              <div className="row">
                <div className="grow"><Field label="Duration (s)"><NumField allowExpr={false} value={opt("time_period")} onChange={(v) => setOpt("time_period", v ?? 60)} /></Field></div>
                <div className="grow"><Field label="Time step (s)"><NumField allowExpr={false} value={opt("time_step")} onChange={(v) => setOpt("time_step", v ?? 2)} /></Field></div>
              </div>
            </>}
            {kind === "ccx_dynamic" && <>
              <div className="row">
                <div className="grow"><Field label="Duration (s)"><NumField allowExpr={false} value={opt("dyn_period")} onChange={(v) => setOpt("dyn_period", v ?? 0.01)} /></Field></div>
                <div className="grow"><Field label="Time step (s)"><NumField allowExpr={false} value={opt("dyn_step")} onChange={(v) => setOpt("dyn_step", v ?? 1e-4)} /></Field></div>
              </div>
              <Field label="Load"><Select value={opt("load_profile")} onChange={(v) => setOpt("load_profile", v)} options={[{ value: "step", label: "Suddenly applied (step)" }, { value: "ramp", label: "Ramped over the duration" }]} /></Field>
            </>}
          </div>
        </Section>

        {defs.map((d) => (
          <Section key={d.kind} title={d.label} actions={<button className="btn sm" onClick={() => addBc(d.kind, d.fields)}>＋ Add</button>}>
            <div className="col" style={{ gap: 10 }}>
              {(bcs[d.kind] ?? []).map((b: Json, i: number) => (
                <div key={i} className="col" style={{ gap: 4, borderLeft: `3px solid #${d.color.toString(16).padStart(6, "0")}`, paddingLeft: 8 }}>
                  {isAsm && <Select value={b.component ?? ""} onChange={(v) => patchBc(d.kind, i, { component: Number(v) })} options={(asmMesh?.components ?? []).map((c) => ({ value: c.id, label: c.name }))} />}
                  <div className="row">
                    <div className="grow"><SelectorInput value={b.face} onChange={(v) => patchBc(d.kind, i, { face: v, _pick: undefined })} picked={pickRef && (!isAsm || picked?.component === b.component) ? pickRef : undefined} pickLabel="pick" /></div>
                    <button className="btn ghost sm" onClick={() => delBc(d.kind, i)}>✕</button>
                  </div>
                  {d.fields.map((f) => (
                    f === "dofs"
                      ? <div key={f} className="row">{["X", "Y", "Z"].map((ax, k) => <label key={ax} className="check"><input type="checkbox" checked={b.dofs?.[k] ?? true} onChange={(e) => { const dd = [...(b.dofs ?? [true, true, true])]; dd[k] = e.target.checked; patchBc(d.kind, i, { dofs: dd }); }} />{ax}</label>)}</div>
                      : <Field key={f} label={f}>{f === "vector" ? <VecField n={3} value={b[f]} onChange={(v) => patchBc(d.kind, i, { [f]: v })} /> : <NumField allowExpr={false} value={b[f]} onChange={(v) => patchBc(d.kind, i, { [f]: v ?? 0 })} />}</Field>
                  ))}
                </div>
              ))}
              {!(bcs[d.kind] ?? []).length && <div className="muted small">None</div>}
            </div>
          </Section>
        ))}
        <div className="muted small" style={{ padding: "8px 12px" }}>Faces: viewport picks (stored as persistent references), <span className="mono">&gt;Z</span>, <span className="mono">&lt;X</span>, <span className="mono">near:x,y,z</span>.</div>
        <div style={{ padding: "0 12px 12px" }}>
          <button className="btn primary" disabled={(isAsm ? asmId == null : !part) || ccxMissing || job?.status === "queued" || job?.status === "running"} onClick={start}>▶ Run analysis</button>
          {ccxMissing && <div className="notice warn" style={{ marginTop: 8 }}>{ccx?.hint ?? "CalculiX is not available"}</div>}
        </div>

        {job?.status === "error" && <div className="notice err" style={{ margin: 12 }}>{job.error}</div>}

        {result && <ResultPanel result={result} rKind={rKind} mode={mode} setMode={setMode} surface={surface} fieldKey={fieldKey} setField={setField}
          scaleMul={scaleMul} setScaleMul={setScaleMul} wire={wire} setWire={setWire} />}
      </div>
    </div>
  );
}

function ResultPanel({ result, rKind, mode, setMode, surface, fieldKey, setField, scaleMul, setScaleMul, wire, setWire }: {
  result: Json; rKind: string; mode: number; setMode: (m: number) => void; surface: Json; fieldKey: string; setField: (f: string) => void;
  scaleMul: number; setScaleMul: (s: number) => void; wire: boolean; setWire: (w: boolean) => void;
}) {
  const s = result.summary ?? {};
  return (
    <Section title="Results">
      <div className="col">
        {["static", "ccx_static", "ccx_contact"].includes(rKind) && (
          <div className="kv">
            <span>Max von Mises</span><span>{fmt(s.max_von_mises_mpa, 2)} MPa</span>
            <span>Max displacement</span><span>{fmt(s.max_displacement_mm, 4)} mm</span>
            <span>Yield strength</span><span>{fmt(s.yield_strength_mpa, 0)} MPa</span>
            <span>Safety factor</span><span style={{ color: (s.safety_factor ?? 99) < 1.5 ? "var(--danger)" : "var(--ok)" }}>{s.safety_factor ? fmt(s.safety_factor, 2) : "∞"}</span>
            {s.max_plastic_strain != null && <><span>Max plastic strain</span><span style={{ color: s.yielded ? "var(--danger)" : undefined }}>{fmt(s.max_plastic_strain, 5)}{s.yielded ? " (yielded)" : ""}</span></>}
            {s.max_contact_pressure_mpa != null && <><span>Max contact pressure</span><span>{fmt(s.max_contact_pressure_mpa, 2)} MPa</span></>}
            {s.equilibrium_error_n != null && <><span>Equilibrium error</span><span>{fmt(s.equilibrium_error_n, 4)} N</span></>}
          </div>
        )}
        {s.contact_pairs && <div className="muted small">{s.contact_pairs.length} contact pair(s): {s.contact_pairs.map((p: Json) => `${p.bodies.join(" ↔ ")} (${p.kind}, ${p.type})`).join("; ")}</div>}
        {THERMAL_KINDS.has(rKind) && (
          <div className="kv"><span>Min temperature</span><span>{fmt(s.min_temperature_c, 1)} °C</span><span>Max temperature</span><span>{fmt(s.max_temperature_c, 1)} °C</span>
            {s.time_s != null && <><span>At time</span><span>{fmt(s.time_s, 1)} s</span></>}</div>
        )}
        {rKind === "ccx_dynamic" && (
          <div className="kv"><span>Peak displacement</span><span>{fmt(s.peak_displacement_mm, 4)} mm</span><span>At time</span><span>{fmt(s.peak_time_s, 5)} s</span>
            <span>von Mises at peak</span><span>{fmt(s.max_von_mises_at_peak_mpa, 2)} MPa</span></div>
        )}
        {MODE_KINDS.has(rKind) && (
          <table className="t">
            <thead><tr><th>Mode</th><th className="num">{rKind === "ccx_buckling" ? "Load factor" : "Frequency"}</th></tr></thead>
            <tbody>{(rKind === "ccx_buckling" ? s.buckling_factors : s.frequencies_hz ?? []).map((f: number, i: number) => (
              <tr key={i} style={{ cursor: i < (result.modes?.length ?? 0) ? "pointer" : "default", background: i === mode ? "var(--select)" : undefined }} onClick={() => i < (result.modes?.length ?? 0) && setMode(i)}>
                <td>{i + 1}</td><td className="num">{rKind === "ccx_buckling" ? `× ${fmt(f, 3)}` : `${fmt(f, 1)} Hz`}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {rKind === "ccx_buckling" && <div className="muted small">Critical load = factor × applied load{s.critical_factor ? ` (lowest positive factor ${fmt(s.critical_factor, 3)})` : ""}.</div>}
        {result.history && <History rows={result.history} kind={rKind} />}
        {surface && Object.keys(surface.fields).length > 1 && <Select value={fieldKey} onChange={setField} options={Object.keys(surface.fields).map((k) => ({ value: k, label: FIELD_LABEL[k] ?? k }))} />}
        {surface?.displacements && <Field label={`Deformation scale ×${scaleMul.toFixed(1)}`}><input type="range" min={0} max={4} step={0.1} value={scaleMul} onChange={(e) => setScaleMul(Number(e.target.value))} /></Field>}
        <label className="check"><input type="checkbox" checked={wire} onChange={(e) => setWire(e.target.checked)} />Show mesh facets</label>
        <div className="kv">
          <span>Solver</span><span>{result.solver?.name ?? "native (scikit-fem)"}</span>
          <span>Mesh</span><span>{result.mesh.nodes.toLocaleString()} nodes · {result.mesh.elements.toLocaleString()} {result.mesh.element_type ?? "tets"}</span>
          <span>Element size</span><span>{fmt(result.mesh.element_size_mm, 2)} mm</span>
          <span>Solve time</span><span>{result.solve_time_s} s</span>
        </div>
      </div>
    </Section>
  );
}

/** Small time-history chart (SVG) for transient results. */
function History({ rows, kind }: { rows: Json[]; kind: string }) {
  const thermal = kind === "ccx_thermal_transient";
  const series: [string, string, string][] = thermal
    ? [["max_c", "max", "var(--gh-c5)"], ["mean_c", "mean", "var(--gh-c3)"], ["min_c", "min", "var(--gh-c0)"]]
    : [["max_displacement_mm", "max |u|", "var(--gh-c5)"]];
  const W = 300, H = 120, pad = 26;
  const t = rows.map((r) => r.time_s);
  const vals = rows.flatMap((r) => series.map(([k]) => r[k]));
  const [t0, t1] = [Math.min(...t), Math.max(...t)];
  const [v0, v1] = [Math.min(...vals), Math.max(...vals)];
  const x = (v: number) => pad + ((v - t0) / (t1 - t0 || 1)) * (W - pad - 6);
  const y = (v: number) => H - 16 - ((v - v0) / (v1 - v0 || 1)) * (H - 26);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", background: "var(--input)", border: "1px solid var(--border)" }}>
        <line x1={pad} x2={W - 6} y1={H - 16} y2={H - 16} stroke="var(--border-strong)" />
        <line x1={pad} x2={pad} y1={10} y2={H - 16} stroke="var(--border-strong)" />
        {series.map(([k, , c]) => <polyline key={k} fill="none" stroke={c} strokeWidth={1.8} points={rows.map((r) => `${x(r.time_s)},${y(r[k])}`).join(" ")} />)}
        <text x={pad} y={H - 3} fontSize={9} fill="var(--muted)">{fmt(t0, 3)} s</text>
        <text x={W - 6} y={H - 3} fontSize={9} fill="var(--muted)" textAnchor="end">{fmt(t1, 3)} s</text>
        <text x={2} y={14} fontSize={9} fill="var(--muted)">{fmt(v1, 3)}</text>
        <text x={2} y={H - 18} fontSize={9} fill="var(--muted)">{fmt(v0, 3)}</text>
      </svg>
      <div className="row small muted" style={{ gap: 12 }}>{series.map(([k, l, c]) => <span key={k}><span style={{ color: c }}>━</span> {l}</span>)}<span className="grow" />{thermal ? "°C" : "mm"}</div>
    </div>
  );
}
