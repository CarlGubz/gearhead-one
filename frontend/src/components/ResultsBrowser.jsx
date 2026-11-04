import React, { useMemo } from "react";
import { TreeNode } from "./BrowserPanel.jsx";
import { ANALYSES } from "./AnalysisPanel.jsx";
import { Summary } from "./JobPanel.jsx";
import { availableFields, fmt, frameCount } from "./utils.js";

const when = (iso) => {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

/** PrePoMax / CalculiX names: field key -> [output group, component]. Anything not listed goes under OTHER. */
const FIELD_GROUPS = {
  displacement_magnitude: ["DISP", "ALL"], ux: ["DISP", "U1"], uy: ["DISP", "U2"], uz: ["DISP", "U3"],
  rotation_magnitude: ["ROT", "ALL"], rx: ["ROT", "UR1"], ry: ["ROT", "UR2"], rz: ["ROT", "UR3"],
  von_mises: ["STRESS", "MISES"], tresca: ["STRESS", "TRESCA"], principal_max: ["STRESS", "S1 (max principal)"],
  principal_mid: ["STRESS", "S2 (mid principal)"], principal_min: ["STRESS", "S3 (min principal)"],
  sxx: ["STRESS", "S11"], syy: ["STRESS", "S22"], szz: ["STRESS", "S33"], sxy: ["STRESS", "S12"], syz: ["STRESS", "S23"], szx: ["STRESS", "S31"],
  von_mises_top: ["STRESS", "MISES top"], von_mises_bottom: ["STRESS", "MISES bottom"], bending_stress: ["STRESS", "Bending (extreme fibre)"],
  exx: ["TOSTRAIN", "E11"], eyy: ["TOSTRAIN", "E22"], ezz: ["TOSTRAIN", "E33"], exy: ["TOSTRAIN", "E12"], eyz: ["TOSTRAIN", "E23"], ezx: ["TOSTRAIN", "E31"],
  equivalent_strain: ["TOSTRAIN", "Equivalent"], plastic_strain: ["PE", "PEEQ"], strain_energy_density: ["ENER", "Strain energy density"],
  axial_force: ["SF", "Axial"], shear_force: ["SF", "Shear"], torsion: ["SF", "Torque"], bending_moment: ["SF", "Bending moment"],
  shell_nx: ["SF", "Nx"], shell_ny: ["SF", "Ny"], shell_nxy: ["SF", "Nxy"], shell_mx: ["SF", "Mx"], shell_my: ["SF", "My"],
  shell_mxy: ["SF", "Mxy"], shell_shear: ["SF", "Transverse shear"],
  contact_pressure: ["CONTACT", "CPRESS"], contact_gap: ["CONTACT", "Gap"],
  temperature: ["NDTEMP", "T"], flux_magnitude: ["HFL", "ALL"], flux_x: ["HFL", "HFL1"], flux_y: ["HFL", "HFL2"], flux_z: ["HFL", "HFL3"],
};

/**
 * Results tree (Analysis tab): runs of the active model, then - like PrePoMax's Results tree - Field Outputs grouped
 * by output (DISP > ALL, U1...; click one to contour it), modes / load factors or time frames, history outputs,
 * summary and export.
 */
export default function ResultsBrowser({ project, job, jobs, solving, sel, setSel, onSolve, onLoadJob, onExport, onSpotlight, showRun = true }) {
  const results = job?.status === "completed" ? job.results : null;
  const type = results?.analysis_type;
  const fields = useMemo(() => availableFields(results), [results]);
  const groups = useMemo(() => {
    const out = new Map();
    fields.forEach((f) => {
      const [g, c] = FIELD_GROUPS[f.key] || ["OTHER", f.label];
      if (!out.has(g)) out.set(g, []);
      out.get(g).push({ ...f, short: c });
    });
    return [...out.entries()];
  }, [fields]);
  const nFrames = frameCount(results);
  const label = (t) => ANALYSES.find((a) => a.value === t)?.label || t;

  if (!project) return null;

  return (
    <>
      {showRun && <div className="btn-row ptree-actions">
        <button className="primary" style={{ marginTop: 0 }} onClick={onSolve} disabled={solving || !project.elements?.length}>
          {solving ? "Solving…" : "Run analysis"}
        </button>
      </div>}
      <ul className="tree ptree">
        <TreeNode label="Results" icon="results">
          {job && job.status !== "completed" && (
            <li className={`tnode-note ${job.status === "failed" ? "msg-error" : ""}`}>
              Run {job.status}{job.status === "running" ? ` · ${Math.round((job.progress || 0) * 100)} %` : ""}
              {job.status === "failed" && " — see Messages for the solver log."}
            </li>
          )}
          <TreeNode label="Runs" count={jobs.length} icon="history" defaultOpen={jobs.length > 1}>
            {jobs.length === 0 && <li className="tnode-note">No runs of this model yet.</li>}
            {jobs.map((j) => (
              <TreeNode key={j.id} active={job?.id === j.id} icon={j.status === "completed" ? "results" : "analysis"}
                label={label(j.analysis_type || project.analysis_type)} meta={j.status === "completed" ? when(j.finished_at || j.created_at) : j.status}
                onClick={j.status === "completed" ? () => onLoadJob(j) : undefined} />
            ))}
          </TreeNode>
          {!results && <li className="tnode-note">Run the analysis to see field outputs here.</li>}
          {results && fields.length > 0 && (
            <TreeNode label="Field Outputs" count={groups.length} icon="field" title={label(type)}>
              {groups.map(([g, items]) => (
                <TreeNode key={g} label={g} icon="field" defaultOpen={items.some((f) => f.key === sel.field)}>
                  {items.map((f) => (
                    <TreeNode key={f.key} label={f.short} meta={f.unit || null} title={f.label} active={sel.field === f.key}
                      onClick={() => setSel({ field: f.key })} />
                  ))}
                </TreeNode>
              ))}
            </TreeNode>
          )}
          {results && (type === "modal" || type === "buckling") && (
            <TreeNode label={type === "modal" ? "Modes" : "Buckling factors"} count={(type === "modal" ? results.frequencies_hz : results.load_factors).length} icon="layers">
              {(type === "modal" ? results.frequencies_hz : results.load_factors).map((v, i) => (
                <TreeNode key={i} label={type === "modal" ? `Mode ${i + 1}` : `λ${i + 1}`} active={sel.frame === i}
                  meta={type === "modal" ? `${fmt(v)} Hz` : fmt(v)} onClick={() => setSel({ frame: i })} />
              ))}
            </TreeNode>
          )}
          {results && (type === "dynamic" || type === "thermal_transient") && nFrames > 1 && (
            <TreeNode label="Time frames" count={nFrames} icon="history" defaultOpen={false}>
              <li className="tnode-note">
                t = {fmt(results.frame_times?.[sel.frame || 0])} s (frame {(sel.frame || 0) + 1}/{nFrames})
                <input type="range" min="0" max={nFrames - 1} value={sel.frame || 0}
                  onChange={(e) => setSel({ frame: parseInt(e.target.value, 10) })} />
              </li>
            </TreeNode>
          )}
          {results?.history && (
            <TreeNode label="History Outputs" count={Object.keys(results.history).length} icon="analysis" defaultOpen={false}>
              {Object.keys(results.history).map((k) => <TreeNode key={k} label={k.replace(/_/g, " ")} onClick={() => onSpotlight("solve")} />)}
            </TreeNode>
          )}
          {results && (
            <TreeNode label="Summary" icon="post" defaultOpen={false}>
              <li className="tnode-note"><Summary r={results} /></li>
            </TreeNode>
          )}
          {results && (
            <TreeNode label="Export" icon="export" defaultOpen={false}>
              <li className="tnode-note btn-row">
                <button onClick={() => onExport("csv")}>CSV</button>
                <button onClick={() => onExport("vtk")}>VTK</button>
                <button onClick={() => onExport("png")}>PNG</button>
              </li>
            </TreeNode>
          )}
        </TreeNode>
      </ul>
    </>
  );
}
