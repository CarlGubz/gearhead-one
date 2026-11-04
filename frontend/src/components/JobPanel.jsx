import React, { useEffect, useMemo, useState } from "react";
import { Check, Num, Select } from "./fields.jsx";
import HistoryChart from "./HistoryChart.jsx";
import { availableFields, fmt, frameCount } from "./utils.js";

const AXIS = [{ value: "0", label: "X" }, { value: "1", label: "Y" }, { value: "2", label: "Z" }];

export function Summary({ r }) {
  const rows = [];
  const t = r.analysis_type;
  if (r.max_displacement !== undefined) rows.push(["Max displacement", `${fmt(r.max_displacement)} m`]);
  if (r.max_von_mises !== undefined) rows.push(["Max von Mises", `${fmt(r.max_von_mises / 1e6)} MPa`]);
  if (r.max_rotation !== undefined) rows.push(["Max rotation", `${fmt(r.max_rotation)} rad`]);
  if (r.max_plastic_strain !== undefined) rows.push(["Max plastic strain", fmt(r.max_plastic_strain)]);
  if (r.contact) {
    rows.push(["Contact nodes", String(r.contact.active_nodes)]);
    rows.push(["Contact force", fmt(r.contact.total_force) + " N"]);
    rows.push(["Max penetration", fmt(r.contact.max_penetration) + " m"]);
  }
  if (r.max_temperature !== undefined) rows.push(["Temperature", `${fmt(r.min_temperature)} … ${fmt(r.max_temperature)}`]);
  if (r.strain_energy !== undefined) rows.push(["Strain energy", `${fmt(r.strain_energy)} J`]);
  if (r.reaction_total && t !== "dynamic") rows.push(["Σ reaction", `(${r.reaction_total.map((v) => fmt(v, 3)).join(", ")}) N`]);
  if (!rows.length) return null;
  const ef = r.element_forces || [];
  return (
    <>
      <table className="kv">
        <tbody>{rows.map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody>
      </table>
      {ef.length > 0 && (
        <details>
          <summary>Member forces ({ef.length})</summary>
          <div style={{ maxHeight: 220, overflow: "auto" }}>
            <table className="kv">
              <thead><tr>{(r.element_force_columns || []).map((c) => <th key={c} style={{ textAlign: "left" }}>{c}</th>)}</tr></thead>
              <tbody>
                {ef.slice(0, 300).map((row) => <tr key={row[0]}>{row.map((v, i) => <td key={i}>{i === 0 ? v : fmt(v, 4)}</td>)}</tr>)}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </>
  );
}

export default function JobPanel({
  onSolve, solving, job, project, sel, setSel, view, setView, display, fieldRange, probe,
  onExport, onAutoScale, onClearResults,
}) {
  const results = job?.status === "completed" ? job.results : null;
  const type = results?.analysis_type;
  const fields = useMemo(() => availableFields(results), [results]);
  const nFrames = frameCount(results);
  const [play, setPlay] = useState(false);

  // frame playback for time-history results
  useEffect(() => {
    if (!play || !(type === "dynamic" || type === "thermal_transient") || nFrames < 2) return undefined;
    const id = setInterval(() => setSel({ frame: ((sel.frame || 0) + 1) % nFrames }), 120);
    return () => clearInterval(id);
  }, [play, type, nFrames, sel.frame, setSel]);

  return (
    <>
      <button className="primary" onClick={onSolve} disabled={solving || !project?.elements?.length}>
        {solving ? "Solving…" : "Run analysis"}
      </button>

      {job && (
        <div className="job-status">
          <p>
            Status: <strong>{job.status}</strong>
            {job.solver_used && <span className="tag">{job.solver_used}</span>}
          </p>
          {(job.status === "running" || job.status === "pending") && (
            <div className="progress-bar"><div className="progress-fill" style={{ width: `${(job.progress || 0) * 100}%` }} /></div>
          )}
          {job.status === "failed" && <pre className="error-log">{job.log}</pre>}
        </div>
      )}

      {results && (
        <>
          <Summary r={results} />

          {(type === "modal" || type === "buckling") && (
            <div className="list-block">
              <h4>{type === "modal" ? "Natural frequencies" : "Buckling load factors"}</h4>
              <ul>
                {(type === "modal" ? results.frequencies_hz : results.load_factors).map((v, i) => (
                  <li key={i} className={sel.frame === i ? "active" : ""} onClick={() => setSel({ frame: i })} style={{ cursor: "pointer" }}>
                    <span>{type === "modal" ? `Mode ${i + 1}` : `λ${i + 1}`}</span>
                    <span>{type === "modal" ? `${fmt(v)} Hz` : fmt(v)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {fields.length > 0 && (
            <Select label="Contour" value={sel.field} onChange={(v) => setSel({ field: v })}
              options={[
                ...(type === "dynamic" ? [{ value: "displacement_magnitude", label: "Displacement magnitude (per frame)" }] : []),
                ...fields.filter((f) => !(type === "dynamic" && f.key === "displacement_magnitude"))
                  .map((f) => ({ value: f.key, label: f.label + (f.unit ? ` [${f.unit}]` : "") })),
              ]} />
          )}

          {(type === "dynamic" || type === "thermal_transient") && nFrames > 1 && (
            <div className="field-row">
              <label>Time: {fmt(results.frame_times?.[sel.frame || 0])} s (frame {(sel.frame || 0) + 1}/{nFrames})</label>
              <div className="btn-row">
                <button onClick={() => setPlay((p) => !p)}>{play ? "❚❚ Pause" : "▶ Play"}</button>
                <input type="range" min="0" max={nFrames - 1} value={sel.frame || 0}
                  onChange={(e) => { setPlay(false); setSel({ frame: parseInt(e.target.value, 10) }); }} />
              </div>
            </div>
          )}

          {display.disp && (
            <>
              <div className="field-row">
                <label>Deformation scale: {fmt(view.deformScale, 3)}×</label>
                <input type="range" min="0" max="1" step="0.001"
                  value={Math.sqrt(Math.min(1, view.deformScale / Math.max(view.scaleMax, 1)))}
                  onChange={(e) => setView({ deformScale: Math.max(0, Math.pow(parseFloat(e.target.value), 2) * view.scaleMax) })} />
                <div className="btn-row">
                  <button onClick={onAutoScale}>Auto</button>
                  <button onClick={() => setView({ deformScale: 1 })}>1×</button>
                  <button onClick={() => setView({ deformScale: 0 })}>0</button>
                </div>
              </div>
              <Check label="Animate" checked={view.animate} onChange={(v) => setView({ animate: v })} />
              <Check label="Show undeformed shape" checked={view.showUndeformed} onChange={(v) => setView({ showUndeformed: v })} />
            </>
          )}

          <Check label="Show mesh edges" checked={view.showEdges} onChange={(v) => setView({ showEdges: v })} />
          {display.field && (
            <>
              <Check label="Mark min / max" checked={view.showMinMax} onChange={(v) => setView({ showMinMax: v })} />
              <Check label="Fixed legend range" checked={!view.rangeAuto} onChange={(v) => setView({ rangeAuto: !v, rangeMin: fieldRange[0], rangeMax: fieldRange[1] })} />
              {!view.rangeAuto && (
                <div className="vec3">
                  <Num value={view.rangeMin} onCommit={(v) => setView({ rangeMin: v })} />
                  <Num value={view.rangeMax} onCommit={(v) => setView({ rangeMax: v })} />
                </div>
              )}
              <Check label="Probe: click a node for its values" checked={view.probeMode} onChange={(v) => setView({ probeMode: v })} />
              {probe && (
                <table className="kv">
                  <tbody>
                    <tr><td>Node</td><td>{probe.node}</td></tr>
                    {probe.rows.map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}
                  </tbody>
                </table>
              )}
            </>
          )}

          <fieldset>
            <legend>Section plane</legend>
            <Check label="Enabled" checked={view.clip.enabled} onChange={(v) => setView({ clip: { ...view.clip, enabled: v } })} />
            {view.clip.enabled && (
              <>
                <Select value={String(view.clip.axis)} onChange={(v) => setView({ clip: { ...view.clip, axis: +v } })} options={AXIS} />
                <input type="range" min="0" max="1" step="0.01" value={view.clip.pos}
                  onChange={(e) => setView({ clip: { ...view.clip, pos: parseFloat(e.target.value) } })} />
                <Check label="Flip side" checked={view.clip.flip} onChange={(v) => setView({ clip: { ...view.clip, flip: v } })} />
              </>
            )}
          </fieldset>

          {results.history && <HistoryChart times={results.times} series={results.history} />}

          <h4>Export</h4>
          <div className="btn-row">
            <button onClick={() => onExport("csv")}>CSV</button>
            <button onClick={() => onExport("vtk")}>VTK</button>
            <button onClick={() => onExport("png")}>PNG</button>
            <button onClick={onClearResults}>Clear results</button>
          </div>
        </>
      )}
    </>
  );
}
