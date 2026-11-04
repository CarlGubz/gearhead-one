import React from "react";
import { Check, Num, Select, Vec3 } from "./fields.jsx";

const asOptions = (list) => (list || []).map((o) => ({ value: String(o.id), label: o.label }));

/** Starting values for a new refinement region (metres). */
const NEW_REGION = {
  ball: { shape: "ball", center: [0, 0, 0], radius: 0.1, size_in: 0.02, size_out: 0, thickness: 0 },
  box: { shape: "box", min: [-0.1, -0.1, -0.1], max: [0.1, 0.1, 0.1], size_in: 0.02, size_out: 0, thickness: 0 },
};

function RegionEditor({ regions, max, onChange }) {
  const set = (i, patch) => onChange(regions.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  return (
    <>
      <h4>Local refinement</h4>
      <p className="hint">
        Smaller elements inside a ball or a box (metres, model coordinates), growing back to the normal size over the transition
        thickness. Use it around a load point, a hole or a fillet.
      </p>
      {regions.map((r, i) => (
        <div className="region" key={i}>
          <div className="region-head">
            <Select value={r.shape} onChange={(v) => onChange(regions.map((q, k) => (k === i ? { ...NEW_REGION[v], size_in: q.size_in } : q)))}
              options={[{ value: "ball", label: "Ball" }, { value: "box", label: "Box" }]} />
            <button className="link" title="Remove this region" onClick={() => onChange(regions.filter((_, k) => k !== i))}>✕</button>
          </div>
          {r.shape === "ball" ? (
            <>
              <Vec3 label="Centre" unit="m" value={r.center} onChange={(v) => set(i, { center: v })} />
              <Num label="Radius" unit="m" min={1e-9} value={r.radius} onCommit={(v) => set(i, { radius: v })} />
            </>
          ) : (
            <>
              <Vec3 label="Corner (min)" unit="m" value={r.min} onChange={(v) => set(i, { min: v })} />
              <Vec3 label="Corner (max)" unit="m" value={r.max} onChange={(v) => set(i, { max: v })} />
            </>
          )}
          <Num label="Element size inside" unit="m" min={1e-9} value={r.size_in} onCommit={(v) => set(i, { size_in: v })} />
          <Num label="Size outside (0 = normal)" unit="m" min={0} value={r.size_out} onCommit={(v) => set(i, { size_out: v })} />
          <Num label="Transition thickness (0 = automatic)" unit="m" min={0} value={r.thickness} onCommit={(v) => set(i, { thickness: v })} />
        </div>
      ))}
      <div className="btn-row">
        <button disabled={regions.length >= max} onClick={() => onChange([...regions, { ...NEW_REGION.ball }])}>+ Ball</button>
        <button disabled={regions.length >= max} onClick={() => onChange([...regions, { ...NEW_REGION.box }])}>+ Box</button>
      </div>
    </>
  );
}

/**
 * Every Gmsh meshing option the server accepts (see backend/app/fea/gmsh_options.py), as a form.
 *   caps       capabilities.gmsh (option catalogue)
 *   value      current options (the catalogue defaults are merged in by the caller)
 *   onChange   (patch) => void
 *   hex        bricks are being made: show the recombination / brick-method choices
 *   quadratic  second-order elements: show the mid-side node controls
 */
export default function GmshOptions({ caps, value, onChange, hex = false, quadratic = true }) {
  const cat = caps?.options;
  if (!cat) return null;
  const v = { ...cat.defaults, ...value };
  const set = (patch) => onChange(patch);
  return (
    <div className="gmsh-opts">
      <h4>Size</h4>
      <Num label="Element size (0 = automatic)" unit="m" min={0} value={v.size} onCommit={(x) => set({ size: x })}
        hint="Largest element edge. With 0 the mesh density slider sets it (smallest dimension / density)." />
      <Num label="Smallest element (0 = automatic)" unit="m" min={0} value={v.min_size} onCommit={(x) => set({ min_size: x })} />
      <Num label="Size factor" min={0.05} max={20} value={v.size_factor} onCommit={(x) => set({ size_factor: x })}
        hint="Multiplies every size above (below 1 = finer)." />
      <Num label="Elements per 2π of curvature (0 = off)" min={0} max={60} step={1} value={v.curvature_points}
        onCommit={(x) => set({ curvature_points: Math.round(x) })} hint="Refines curved surfaces: about 12–20 follows a circle well." />
      <Check label="Size from CAD points" checked={v.size_from_points} onChange={(x) => set({ size_from_points: x })} />
      <Check label="Extend size from the boundary" checked={v.extend_from_boundary} onChange={(x) => set({ extend_from_boundary: x })} />

      <h4>Algorithms</h4>
      <Select label="Surface (2-D)" value={String(v.algorithm_2d)} onChange={(x) => set({ algorithm_2d: Number(x) })}
        options={asOptions(cat.algorithms_2d)} />
      <Select label="Volume (3-D)" value={String(v.algorithm_3d)} onChange={(x) => set({ algorithm_3d: Number(x) })}
        options={asOptions(cat.algorithms_3d)} />
      {hex && (
        <>
          <Select label="Bricks made by" value={v.hex_method} onChange={(x) => set({ hex_method: x })} options={asOptions(cat.hex_methods)} />
          <Select label="Quad recombination" value={String(v.recombine_algorithm)} onChange={(x) => set({ recombine_algorithm: Number(x) })}
            options={asOptions(cat.recombine_algorithms)} />
          <p className="hint">
            Swept bricks extrude a quad mesh along the axis (box, cylinder, tube, plate with hole, L-bracket). Every other shape
            and every CAD file is meshed with tetrahedra first and each one is split into four bricks.
          </p>
        </>
      )}

      <h4>Quality</h4>
      <Num label="Smoothing steps" min={0} max={20} step={1} value={v.smoothing_steps} onCommit={(x) => set({ smoothing_steps: Math.round(x) })} />
      <Select label="Optimiser" value={v.optimize} onChange={(x) => set({ optimize: x })} options={asOptions(cat.optimizers)} />
      <Num label="Uniform refinements" min={0} max={2} step={1} value={v.refine} onCommit={(x) => set({ refine: Math.round(x) })}
        hint="Each pass splits every element (×8 tetrahedra / bricks): use sparingly." />
      {quadratic && (
        <>
          <Check label="Straight mid-side nodes (do not curve onto the CAD surface)" checked={v.straight_midside}
            onChange={(x) => set({ straight_midside: x })} />
          <Select label="Curved element optimisation" value={v.curve_optimize} onChange={(x) => set({ curve_optimize: x })}
            options={asOptions(cat.curve_optimizers)} disabled={v.straight_midside} />
        </>
      )}

      <RegionEditor regions={v.regions || []} max={cat.max_regions || 8} onChange={(r) => set({ regions: r })} />
    </div>
  );
}

/** The options object to send to the server: everything the form holds, without the element family / order (the caller sets those). */
export function gmshRequestOptions(caps, value, extra = {}) {
  const base = { ...(caps?.options?.defaults || {}), ...(value || {}) };
  delete base.family;
  delete base.order;
  return { ...base, ...extra };
}
