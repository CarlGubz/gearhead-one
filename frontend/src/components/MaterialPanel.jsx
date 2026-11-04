import React, { useEffect, useState } from "react";
import { Num, Select } from "./fields.jsx";

export const PRESETS = {
  Steel: { E: 210e9, nu: 0.3, density: 7850, conductivity: 50, specific_heat: 490, alpha: 12e-6, yield_stress: 250e6, hardening: 2e9 },
  Stainless_Steel: { E: 193e9, nu: 0.3, density: 8000, conductivity: 16, specific_heat: 500, alpha: 17e-6, yield_stress: 215e6, hardening: 2e9 },
  Aluminum: { E: 69e9, nu: 0.33, density: 2700, conductivity: 205, specific_heat: 900, alpha: 23e-6, yield_stress: 275e6, hardening: 0.8e9 },
  Titanium: { E: 114e9, nu: 0.34, density: 4500, conductivity: 21.9, specific_heat: 520, alpha: 8.6e-6, yield_stress: 880e6, hardening: 1.5e9 },
  Copper: { E: 110e9, nu: 0.34, density: 8960, conductivity: 401, specific_heat: 385, alpha: 17e-6, yield_stress: 70e6, hardening: 1e9 },
  Cast_Iron: { E: 110e9, nu: 0.28, density: 7200, conductivity: 55, specific_heat: 460, alpha: 10.5e-6, yield_stress: 0, hardening: 0 },
  ABS_Plastic: { E: 2.3e9, nu: 0.35, density: 1040, conductivity: 0.25, specific_heat: 1300, alpha: 90e-6, yield_stress: 40e6, hardening: 0.1e9 },
  Concrete: { E: 30e9, nu: 0.2, density: 2400, conductivity: 1.7, specific_heat: 880, alpha: 10e-6, yield_stress: 0, hardening: 0 },
  Glass: { E: 70e9, nu: 0.22, density: 2500, conductivity: 1.05, specific_heat: 840, alpha: 9e-6, yield_stress: 0, hardening: 0 },
};

/**
 * Material library editor. "default" is the project's base material (project.material); other entries live in
 * project.materials. Parts pick one of them in the Parts panel.
 */
export default function MaterialPanel({ material, materials, parts, onUpdate, onAdd, onDelete }) {
  const lib = materials || [];
  const [editing, setEditing] = useState("default");
  const id = lib.some((x) => x.id === editing) ? editing : "default";
  const current = id === "default" ? material : lib.find((x) => x.id === id);
  const [m, setM] = useState(current || { name: "Steel", ...PRESETS.Steel });

  useEffect(() => {
    if (current) setM(current);
  }, [current]);

  const usedBy = (parts || []).filter((p) => (p.material_id || "default") === id).map((p) => p.name);
  const commit = (patch) => {
    const next = { ...m, ...patch };
    setM(next);
    onUpdate(id, next);
  };

  return (
    <>
      <p className="hint">All values are SI: metres, kilograms, seconds, newtons, pascals, °C.</p>
      <Select label="Material" value={id} onChange={setEditing}
        options={[{ value: "default", label: `${material?.name || "Default"} (default)` },
          ...lib.map((x) => ({ value: x.id, label: x.name }))]} />
      <p className="hint">{usedBy.length ? `Used by: ${usedBy.join(", ")}` : "Not used by any part."}</p>
      <div className="btn-row">
        <button onClick={() => onAdd({ ...m, name: `${m.name || "Material"} copy`.slice(0, 60) }).then?.((p) => p && setEditing(p.materials[p.materials.length - 1].id))}
          title="Add a new material starting from the values below">+ New</button>
        <button disabled={id === "default"}
          onClick={() => window.confirm(`Delete “${m.name}”? Parts using it fall back to the default material.`) && onDelete(id)}>Delete</button>
      </div>

      <div className="field-row">
        <label>Name</label>
        <input type="text" defaultValue={m.name} key={`${id}:${m.name}`}
          onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== m.name && commit({ name: e.target.value.trim().slice(0, 60) })}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
      </div>
      <Select label="Preset" value={PRESETS[m.name] ? m.name : "custom"}
        onChange={(v) => v !== "custom" && commit({ name: v, ...PRESETS[v] })}
        options={[...Object.keys(PRESETS).map((k) => ({ value: k, label: k.replace(/_/g, " ") })),
          { value: "custom", label: "Custom", disabled: true }]} />

      <h4>Elastic &amp; mass</h4>
      <Num label="Young's modulus E" unit="Pa" value={m.E} min={1} onCommit={(v) => commit({ E: v })} />
      <Num label="Poisson's ratio" value={m.nu} min={-0.99} max={0.499} onCommit={(v) => commit({ nu: v })} />
      <Num label="Density" unit="kg/m³" value={m.density} min={0} onCommit={(v) => commit({ density: v })} />

      <h4>Thermal</h4>
      <Num label="Conductivity" unit="W/(m·K)" value={m.conductivity} min={0} onCommit={(v) => commit({ conductivity: v })} />
      <Num label="Specific heat" unit="J/(kg·K)" value={m.specific_heat} min={0} onCommit={(v) => commit({ specific_heat: v })} />
      <Num label="Expansion coeff. α" unit="1/K" value={m.alpha ?? 0} onCommit={(v) => commit({ alpha: v })} />

      <h4>Plasticity (J2, linear hardening)</h4>
      <Num label="Yield stress" unit="Pa" value={m.yield_stress ?? 0} min={0} onCommit={(v) => commit({ yield_stress: v })}
        hint="0 = purely elastic. Used by the Nonlinear analysis." />
      <Num label="Hardening modulus H" unit="Pa" value={m.hardening ?? 0} min={0} onCommit={(v) => commit({ hardening: v })} />
    </>
  );
}
