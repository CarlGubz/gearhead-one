import React, { useEffect, useState } from "react";
import { Check, Num, Select, Vec3 } from "./fields.jsx";
import { parseIdList } from "./utils.js";

export const ANALYSES = [
  { value: "static", label: "Static structural", free: true, mech: true },
  { value: "nonlinear", label: "Static nonlinear (plasticity)", mech: true },
  { value: "modal", label: "Frequency (natural modes)", mech: true },
  { value: "buckling", label: "Linear buckling", mech: true },
  { value: "dynamic", label: "Dynamic (implicit, Newmark)", mech: true },
  { value: "thermomech", label: "Thermo-mechanical (heat → stress)", mech: true },
  { value: "thermal", label: "Heat transfer (steady)" },
  { value: "thermal_transient", label: "Heat transfer (transient)" },
];

const HINTS = {
  static: "Linear elastic. Supports + loads (force, pressure, gravity, centrifugal) and an optional uniform temperature change.",
  nonlinear: "J2 plasticity with linear hardening, and frictionless contact between parts. Needs a material yield stress for plasticity; the load is applied in increments with Newton iterations.",
  modal: "Natural frequencies and mode shapes. With no supports you get a free-free analysis (six ~0 Hz rigid-body modes).",
  buckling: "Eigenvalue buckling. The load you apply is the reference load; results are the factors that scale it to the buckling load.",
  dynamic: "Transient response to the applied loads scaled by the amplitude curve, with optional Rayleigh damping.",
  thermomech: "Solves the temperature field first, then the thermal stresses relative to the reference temperature. Needs temperature BCs and supports.",
  thermal: "Steady-state conduction with fixed temperatures, heat sources, convection and radiation.",
  transient_thermal: "",
  thermal_transient: "Transient conduction (θ-method). Set the initial temperature and duration.",
};

function Amplitude({ value, onChange }) {
  const a = value || { type: "step" };
  return (
    <>
      <Select label="Load amplitude over time" value={a.type} onChange={(t) => onChange({ ...a, type: t })}
        options={[{ value: "step", label: "Step (constant)" }, { value: "ramp", label: "Ramp up" },
          { value: "sine", label: "Sine" }, { value: "pulse", label: "Pulse" }]} />
      {a.type === "ramp" && <Num label="Ramp duration" unit="s" value={a.duration ?? 1} min={1e-9} onCommit={(v) => onChange({ ...a, duration: v })} />}
      {a.type === "pulse" && <Num label="Pulse duration" unit="s" value={a.duration ?? 1} min={1e-9} onCommit={(v) => onChange({ ...a, duration: v })} />}
      {a.type === "sine" && <Num label="Frequency" unit="Hz" value={a.frequency ?? 1} min={1e-9} onCommit={(v) => onChange({ ...a, frequency: v })} />}
    </>
  );
}

function HistoryNodes({ value, onChange }) {
  const [text, setText] = useState((value || []).join(", "));
  useEffect(() => setText((value || []).join(", ")), [value]);
  return (
    <div className="field-row">
      <label>History output nodes</label>
      <input type="text" value={text} placeholder="e.g. 12, 40-42" onChange={(e) => setText(e.target.value)}
        onBlur={() => onChange(parseIdList(text))} />
    </div>
  );
}

const STRUCT_ANALYSES = ["static", "modal", "buckling", "dynamic"];

export default function AnalysisPanel({ analysisType, settings, gravity, isPro, onChangeType, onSettings, onGravity, structural }) {
  const st = settings || {};
  const [g, setG] = useState(gravity || { enabled: false, x: 0, y: 0, z: -9.81 });
  useEffect(() => setG(gravity || { enabled: false, x: 0, y: 0, z: -9.81 }), [gravity]);
  const cf = st.centrifugal || { enabled: false, rpm: 0, axis: [0, 0, 1], origin: [0, 0, 0] };
  const kind = analysisType || "static";
  const mech = ANALYSES.find((a) => a.value === kind)?.mech;
  const timed = ["dynamic", "thermal_transient"].includes(kind) || (kind === "thermomech" && st.thermal_mode === "transient");
  const loadsGravity = ["static", "nonlinear", "dynamic", "thermomech"].includes(kind);

  return (
    <>
      <Select label="Analysis type" value={kind} onChange={onChangeType}
        options={ANALYSES.filter((a) => !structural || STRUCT_ANALYSES.includes(a.value))
          .map((a) => ({ value: a.value, label: a.label + (a.free || isPro ? "" : " — Pro") }))} />
      <p className="hint">{HINTS[kind]}</p>
      {structural && kind === "buckling" && <p className="hint">Buckling is available for truss and beam models.</p>}
      {structural && kind === "modal" && <p className="hint">Add point masses (Loads) for spring–mass systems.</p>}
      {!structural && kind !== "modal" && kind !== "buckling" && (
        <Select label="Linear solver" value={st.linear_solver || "auto"} onChange={(v) => onSettings({ linear_solver: v })}
          options={[{ value: "auto", label: "Automatic (direct, iterative for very large models)" },
            { value: "direct", label: "Direct (sparse LU)" }, { value: "iterative", label: "Iterative (PCG + Schwarz)" }]} />
      )}

      {(kind === "modal" || kind === "buckling") && (
        <Num label="Number of modes" value={st.n_modes ?? 6} min={1} max={kind === "buckling" ? 20 : 50} step="1"
          onCommit={(v) => onSettings({ n_modes: Math.round(v) })} />
      )}
      {!structural && (kind === "static" || kind === "nonlinear") && (
        <>
          <Check label="Large deformation (NLGEOM)" checked={!!st.nlgeom} onChange={(on) => onSettings({ nlgeom: on })} />
          {st.nlgeom && (
            <>
              <Select label="Material model" value={st.finite_strain_model || "svk"} onChange={(v) => onSettings({ finite_strain_model: v })}
                options={[{ value: "svk", label: "Saint Venant–Kirchhoff (large rotations, moderate strain)" },
                  { value: "neohookean", label: "Neo-Hookean (large strain, rubber-like)" }]} />
              <p className="hint">Elastic only (no plasticity or temperature change); loads do not follow the rotation.</p>
            </>
          )}
        </>
      )}
      {!structural && (kind === "nonlinear" || (kind === "static" && st.nlgeom)) && (
        <Num label="Load increments" value={st.increments ?? 10} min={1} max={200} step="1"
          onCommit={(v) => onSettings({ increments: Math.round(v) })} />
      )}
      {!structural && (kind === "static" || kind === "nonlinear") && (
        <>
          <Check label="Apply uniform temperature change" checked={st.uniform_temperature !== undefined}
            onChange={(on) => onSettings(on ? { uniform_temperature: 20, reference_temperature: st.reference_temperature ?? 20 } : { clear: ["uniform_temperature"] })} />
          {st.uniform_temperature !== undefined && (
            <>
              <Num label="Temperature" unit="°C" value={st.uniform_temperature} onCommit={(v) => onSettings({ uniform_temperature: v })} />
              <Num label="Reference (stress-free) temperature" unit="°C" value={st.reference_temperature ?? 20}
                onCommit={(v) => onSettings({ reference_temperature: v })} />
            </>
          )}
        </>
      )}
      {kind === "thermomech" && (
        <>
          <Select label="Heat transfer" value={st.thermal_mode || "steady"} onChange={(v) => onSettings({ thermal_mode: v })}
            options={[{ value: "steady", label: "Steady state" }, { value: "transient", label: "Transient (final state)" }]} />
          <Num label="Reference (stress-free) temperature" unit="°C" value={st.reference_temperature ?? 20}
            onCommit={(v) => onSettings({ reference_temperature: v })} />
        </>
      )}
      {timed && (
        <>
          <Num label="Duration" unit="s" value={st.t_end ?? (kind === "dynamic" ? 0.1 : 100)} min={1e-12} onCommit={(v) => onSettings({ t_end: v })} />
          <Num label="Time steps" value={st.n_steps ?? (kind === "dynamic" ? 200 : 100)} min={1} max={20000} step="1"
            onCommit={(v) => onSettings({ n_steps: Math.round(v) })} />
          <Amplitude value={st.amplitude} onChange={(a) => onSettings({ amplitude: a })} />
          <HistoryNodes value={st.history_nodes} onChange={(ids) => onSettings({ history_nodes: ids })} />
        </>
      )}
      {(kind === "thermal_transient" || (kind === "thermomech" && st.thermal_mode === "transient")) && (
        <>
          <Num label="Initial temperature" unit="°C" value={st.initial_temperature ?? 0} onCommit={(v) => onSettings({ initial_temperature: v })} />
          <Num label="θ (1 = backward Euler, 0.5 = Crank–Nicolson)" value={st.theta ?? 1} min={0.5} max={1} step="0.05"
            onCommit={(v) => onSettings({ theta: v })} />
        </>
      )}
      {kind === "thermal" && (
        <Num label="Initial guess (radiation only)" unit="°C" value={st.initial_temperature ?? 0}
          onCommit={(v) => onSettings({ initial_temperature: v })} />
      )}
      {kind === "dynamic" && (
        <fieldset>
          <legend>Rayleigh damping  C = αM + βK</legend>
          <Num label="α (mass)" unit="1/s" value={st.rayleigh_alpha ?? 0} min={0} onCommit={(v) => onSettings({ rayleigh_alpha: v })} />
          <Num label="β (stiffness)" unit="s" value={st.rayleigh_beta ?? 0} min={0} onCommit={(v) => onSettings({ rayleigh_beta: v })} />
        </fieldset>
      )}

      {mech && loadsGravity && (
        <fieldset>
          <legend>Gravity (self-weight)</legend>
          <Check label="Enabled" checked={g.enabled} onChange={(on) => { const n = { ...g, enabled: on }; setG(n); onGravity(n); }} />
          {g.enabled && (
            <Vec3 label="Acceleration" unit="m/s²" value={[g.x, g.y, g.z]}
              onChange={([x, y, z]) => { const n = { ...g, x, y, z }; setG(n); onGravity(n); }} />
          )}
        </fieldset>
      )}
      {mech && loadsGravity && kind !== "thermomech" && (
        <fieldset>
          <legend>Centrifugal load</legend>
          <Check label="Enabled" checked={cf.enabled} onChange={(on) => onSettings({ centrifugal: { ...cf, enabled: on } })} />
          {cf.enabled && (
            <>
              <Num label="Rotation speed" unit="rpm" value={cf.rpm ?? 0} onCommit={(v) => onSettings({ centrifugal: { ...cf, rpm: v } })} />
              <Vec3 label="Axis direction" value={cf.axis} onChange={(a) => onSettings({ centrifugal: { ...cf, axis: a } })} />
              <Vec3 label="Point on axis" unit="m" value={cf.origin} onChange={(o) => onSettings({ centrifugal: { ...cf, origin: o } })} />
            </>
          )}
        </fieldset>
      )}
    </>
  );
}
