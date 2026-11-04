import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Num, Select } from "./fields.jsx";
import * as api from "../api.js";

/**
 * Transfer command (all three workspaces): moves geometry and loads between the FEA, CFD and CAD workspaces of the
 * open project (backend: app/bridge.py).
 *
 *   → FEA   mesh a CAD part / assembly or the obstacles of a CFD case into an FEA model (new model on the stack)
 *   → CFD   send an FEA model or a CAD part / assembly to a CFD case as solid obstacles (new case or existing one)
 *   → CAD   rebuild an FEA model (primitives as editable features) or CFD obstacles as CAD parts
 *   Loads   map a CFD run's surface pressure / wall temperature onto the active FEA model
 */
const TABS = [
  { id: "fea", label: "→ FEA" },
  { id: "cfd", label: "→ CFD" },
  { id: "cad", label: "→ CAD" },
  { id: "loads", label: "CFD loads" },
];

function Result({ result }) {
  if (!result) return null;
  return (
    <div className="bridge-result">
      <strong>{result.title}</strong>
      {result.notes?.length ? <ul>{result.notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
    </div>
  );
}

export default function BridgePanel({ open, projectId, workspace, cfdCaseId, onFea, onCfd, onCad, notify }) {
  const [tab, setTab] = useState(workspace === "cfd" ? "cfd" : workspace === "cad" ? "cad" : "fea");
  const [src, setSrc] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  // ---- form state per direction
  const [feaSource, setFeaSource] = useState("");
  const [feaSize, setFeaSize] = useState(0);
  const [feaElem, setFeaElem] = useState("tet2");
  const [feaTarget, setFeaTarget] = useState("new_model");
  const [cfdSource, setCfdSource] = useState("");
  const [cfdCase, setCfdCase] = useState("new");
  const [cfdDim, setCfdDim] = useState("3d");
  const [cfdFit, setCfdFit] = useState(true);
  const [cfdFixed, setCfdFixed] = useState(false);
  const [cfdTemp, setCfdTemp] = useState(323.15);
  const [cadSource, setCadSource] = useState("");
  const [loadRun, setLoadRun] = useState("");
  const [loadOb, setLoadOb] = useState("");
  const [loadP, setLoadP] = useState(true);
  const [loadT, setLoadT] = useState(false);
  const [bins, setBins] = useState(12);

  useEffect(() => { if (open) setTab(workspace === "cfd" ? "cfd" : workspace === "cad" ? "cad" : "fea"); }, [open, workspace]);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try { setSrc(await api.bridgeSources(projectId)); } catch (err) { notify?.(api.errorMessage(err), "error"); }
  }, [projectId, notify]);
  useEffect(() => { if (open) { refresh(); setResult(null); } }, [open, refresh]);
  useEffect(() => { if (cfdCaseId) { setCfdCase(cfdCaseId); setCfdFit(false); } }, [cfdCaseId]);

  // ---- options from the project's contents
  const feaModels = src?.fea?.models || [];
  const cadParts = src?.cad?.parts || [];
  const cadAsms = src?.cad?.assemblies || [];
  const cases = src?.cfd?.cases || [];
  const toFeaOptions = useMemo(() => [
    ...cadParts.map((p) => ({ value: `cad_part:${p.id}`, label: `CAD part · ${p.name}` })),
    ...cadAsms.map((a) => ({ value: `cad_assembly:${a.id}`, label: `CAD assembly · ${a.name} (${a.components})` })),
    ...cases.filter((c) => c.obstacles.length).map((c) => ({ value: `cfd_obstacles:${c.id}`, label: `CFD obstacles · ${c.name} (${c.obstacles.length})` })),
  ], [cadParts, cadAsms, cases]);
  const toCfdOptions = useMemo(() => [
    ...feaModels.filter((m) => m.solid).map((m) => ({ value: `fea_model:${m.id}`, label: `FEA model · ${m.name}${m.active ? " (active)" : ""}` })),
    ...cadParts.map((p) => ({ value: `cad_part:${p.id}`, label: `CAD part · ${p.name}` })),
    ...cadAsms.map((a) => ({ value: `cad_assembly:${a.id}`, label: `CAD assembly · ${a.name}` })),
  ], [feaModels, cadParts, cadAsms]);
  const toCadOptions = useMemo(() => [
    ...feaModels.filter((m) => m.solid).map((m) => ({ value: `fea_model:${m.id}`, label: `FEA model · ${m.name}${m.active ? " (active)" : ""}` })),
    ...cases.filter((c) => c.obstacles.length).map((c) => ({ value: `cfd_obstacles:${c.id}`, label: `CFD obstacles · ${c.name}` })),
  ], [feaModels, cases]);
  const runs = useMemo(() => cases.flatMap((c) => c.runs.filter((r) => r.status === "completed" || r.status === "cancelled")
    .map((r) => ({ ...r, case: c }))), [cases]);
  const run = runs.find((r) => r.id === loadRun) || runs[0];
  const pick = (value, options) => (options.some((o) => o.value === value) ? value : options[0]?.value || "");
  const split = (v) => { const i = v.indexOf(":"); return [v.slice(0, i), v.slice(i + 1)]; };

  const go = async (fn) => {
    setBusy(true);
    setResult(null);
    try { await fn(); await refresh(); } catch (err) { notify?.(api.errorMessage(err, "The transfer failed."), "error"); } finally { setBusy(false); }
  };

  const sendToFea = () => go(async () => {
    const [source, id] = split(pick(feaSource, toFeaOptions));
    const [family, order] = { tet1: ["tet", 1], tet2: ["tet", 2], hex1: ["hex", 1], hex2: ["hex", 2] }[feaElem];
    notify?.("Meshing the geometry with Gmsh… this can take up to a minute.");
    const p = await api.bridgeToFea(projectId, { source, id, size: feaSize / 1000, order, family, target: feaTarget });
    setResult({ title: `FEA model “${p.layer_stack?.find((l) => l.active)?.name}”: ${p.nodes.length} nodes, ${p.elements.length} ${p.element_type} elements.` });
    onFea?.(p);
  });
  const sendToCfd = () => go(async () => {
    const [source, id] = split(pick(cfdSource, toCfdOptions));
    const body = { source, dimension: cfdDim, fit_domain: cfdFit, thermal: cfdFixed ? "fixed" : "adiabatic", temperature: cfdTemp };
    if (source === "fea_model") body.layer_id = id; else body.id = id;
    if (cfdCase !== "new") body.case_id = cfdCase;
    const r = await api.bridgeToCfd(projectId, body);
    setResult({ title: `CFD case “${r.case_name}”: added ${r.obstacles.length} solid(s) (${r.triangles.join(", ")} triangles).`, notes: r.notes });
    onCfd?.(r.case_id);
  });
  const sendToCad = () => go(async () => {
    const [source, id] = split(pick(cadSource, toCadOptions));
    const r = await api.bridgeToCad(projectId, source === "fea_model" ? { source, layer_id: id } : { source, id });
    setResult({ title: `Created ${r.parts.length} CAD part(s): ${r.parts.map((p) => p.name).join(", ") || "none"}.`, notes: r.notes });
    onCad?.(r);
  });
  const mapLoads = () => go(async () => {
    const ob = loadOb && run?.obstacles.some((o) => o.id === loadOb) ? loadOb : run?.obstacles[0]?.id;
    const r = await api.bridgeCfdToFea(projectId, { run_id: run.id, obstacle_id: ob, pressure: loadP, temperature: loadT, bins });
    const s = r.summary;
    setResult({
      title: `Mapped ${s.faces_mapped} of ${s.faces_total} surface faces onto the active FEA model.`,
      notes: [
        s.pressure_range_pa && `Pressure ${s.pressure_range_pa.map((v) => v.toPrecision(4)).join(" … ")} Pa as ${loadP ? "pressure loads" : ""}.`,
        s.temperature_range_k && `Wall temperature ${s.temperature_range_k.map((v) => v.toPrecision(5)).join(" … ")} K as temperature supports.`,
        "Faces whose neighbouring cells are all solid (or outside the CFD section in 2-D) get no load.",
      ].filter(Boolean),
    });
    onFea?.(r.project);
  });

  if (!projectId) return <p className="hint">Open a project first (Project in the ribbon).</p>;
  return (
    <div className="bridge-panel">
      <div className="bridge-tabs" role="tablist">
        {TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "on" : ""} onClick={() => { setTab(t.id); setResult(null); }}>{t.label}</button>)}
      </div>

      {tab === "fea" && (
        <>
          <p className="bridge-note">Mesh CAD geometry or the solids of a CFD case for structural analysis. CAD materials come along as FEA materials; assemblies become one part per component (tied automatically).</p>
          {toFeaOptions.length ? (
            <>
              <Select label="Source" value={pick(feaSource, toFeaOptions)} onChange={setFeaSource} options={toFeaOptions} />
              <Select label="Elements" value={feaElem} onChange={setFeaElem} options={[
                { value: "tet2", label: "Tet10 (quadratic tetrahedra)" }, { value: "tet1", label: "Tet4 (linear tetrahedra)" },
                { value: "hex2", label: "Hex20 (quadratic bricks, Gmsh)" }, { value: "hex1", label: "Hex8 (linear bricks, Gmsh)" }]} />
              <Num label="Element size" unit="mm, 0 = automatic" value={feaSize} min={0} onCommit={setFeaSize} />
              <Select label="Into" value={feaTarget} onChange={setFeaTarget} options={[
                { value: "new_model", label: "A new model on top of the model stack" }, { value: "active_model", label: "The active model (replaces its mesh)" }]} />
              <button className="primary" disabled={busy} onClick={sendToFea}>{busy ? "Working…" : "Mesh into FEA"}</button>
            </>
          ) : <p className="hint">Nothing to send yet: create a part in the CAD workspace or an obstacle in a CFD case.</p>}
        </>
      )}

      {tab === "cfd" && (
        <>
          <p className="bridge-note">Flow around an FEA model or a CAD part: each part becomes a solid obstacle (a closed triangle surface rasterised on the CFD mesh). The source position is remembered, so CFD loads map back onto the FEA faces.</p>
          {toCfdOptions.length ? (
            <>
              <Select label="Source" value={pick(cfdSource, toCfdOptions)} onChange={setCfdSource} options={toCfdOptions} />
              <Select label="CFD case" value={cfdCase} onChange={(v) => { setCfdCase(v); setCfdFit(v === "new"); }}
                options={[{ value: "new", label: "New case (wind tunnel around the bodies)" }, ...cases.map((c) => ({ value: c.id, label: `${c.name} (${c.dimension})` }))]} />
              <Select label="Dimension" value={cfdDim} onChange={setCfdDim} options={[{ value: "3d", label: "3-D (whole body)" }, { value: "2d", label: "2-D (section through the centre)" }]} />
              <Check label="Size the domain and mesh around the bodies (inlet left, outlet right)" checked={cfdFit} onChange={setCfdFit} />
              <Check label="Fixed surface temperature (energy equation)" checked={cfdFixed} onChange={setCfdFixed} />
              {cfdFixed && <Num label="Surface temperature" unit="K" value={cfdTemp} onCommit={setCfdTemp} />}
              <button className="primary" disabled={busy} onClick={sendToCfd}>{busy ? "Working…" : "Send to CFD"}</button>
            </>
          ) : <p className="hint">Nothing to send yet: mesh a solid FEA model or create a CAD part first.</p>}
        </>
      )}

      {tab === "cad" && (
        <>
          <p className="bridge-note">FEA primitives (box, cylinder, sphere, cone, tube, plate with hole, L-bracket) become editable CAD features at their current position, a torus an exact solid; other parts become solids sewn from their surface. CFD boxes / cylinders / spheres become features, imported solids go back to their source position.</p>
          {toCadOptions.length ? (
            <>
              <Select label="Source" value={pick(cadSource, toCadOptions)} onChange={setCadSource} options={toCadOptions} />
              <button className="primary" disabled={busy} onClick={sendToCad}>{busy ? "Working…" : "Create CAD parts"}</button>
            </>
          ) : <p className="hint">Nothing to send yet: mesh a solid FEA model or add obstacles to a CFD case.</p>}
        </>
      )}

      {tab === "loads" && (
        <>
          <p className="bridge-note">Apply a finished CFD run to the <b>active FEA model</b>: the fluid pressure on the body becomes pressure loads and the fluid temperature next to it temperature supports, grouped into levels. The active model should be the geometry the obstacle came from.</p>
          {runs.length ? (
            <>
              <Select label="CFD run" value={run?.id || ""} onChange={setLoadRun}
                options={runs.map((r) => ({ value: r.id, label: `${r.case.name} · ${r.name || r.id.slice(0, 6)} (${r.status})` }))} />
              <Select label="Obstacle" value={loadOb && run?.obstacles.some((o) => o.id === loadOb) ? loadOb : run?.obstacles[0]?.id || ""}
                onChange={setLoadOb} options={(run?.obstacles || []).map((o) => ({ value: o.id, label: o.name || o.id }))} />
              <Check label="Surface pressure → pressure loads" checked={loadP} onChange={setLoadP} />
              <Check label="Wall temperature → temperature supports (energy runs)" checked={loadT} onChange={setLoadT} />
              <Num label="Levels" value={bins} min={1} max={64} step={1} onCommit={(v) => setBins(Math.round(v))} />
              <button className="primary" disabled={busy || !run?.obstacles.length} onClick={mapLoads}>{busy ? "Working…" : "Apply to the active FEA model"}</button>
            </>
          ) : <p className="hint">No finished CFD run in this project yet.</p>}
        </>
      )}
      <Result result={result} />
    </div>
  );
}
