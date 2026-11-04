import React from "react";
import { Check, Num, Select, Vec3 } from "./fields.jsx";

export const DEFAULT_SECTIONS = {
  B31: { type: "rect", b: 0.05, h: 0.1 },
  T3D2: { area: 1e-4 },
  S4: { thickness: 0.01 },
  SPRING2: { k: 1e6, c: 0 },
};

const BEAM_TYPES = [
  { value: "rect", label: "Rectangle (b × h)" },
  { value: "circle", label: "Solid circle (d)" },
  { value: "pipe", label: "Pipe (d, wall t)" },
  { value: "general", label: "General (A, Iy, Iz, J)" },
];

const BEAM_DEFAULTS = {
  rect: { type: "rect", b: 0.05, h: 0.1 },
  circle: { type: "circle", d: 0.05 },
  pipe: { type: "pipe", d: 0.06, t: 0.004 },
  general: { type: "general", A: 5e-3, Iy: 4.2e-6, Iz: 1.0e-6, J: 2e-6 },
};

/** Section properties of one structural part. `onChange(section)` is called with the complete new section. */
export default function SectionEditor({ etype, section, onChange }) {
  const sec = section || DEFAULT_SECTIONS[etype] || {};
  const set = (patch) => onChange({ ...sec, ...patch });

  if (etype === "T3D2") {
    return <Num label="Cross-section area A" unit="m²" value={sec.area ?? 1e-4} min={1e-12} onCommit={(v) => set({ area: v })} />;
  }
  if (etype === "S4") {
    return <Num label="Thickness t" unit="m" value={sec.thickness ?? 0.01} min={1e-9} onCommit={(v) => set({ thickness: v })} />;
  }
  if (etype === "SPRING2") {
    return (
      <>
        <Num label="Stiffness k" unit="N/m" value={sec.k ?? 0} min={0} onCommit={(v) => set({ k: v })} />
        <Num label="Damping c (dashpot)" unit="N·s/m" value={sec.c ?? 0} min={0} onCommit={(v) => set({ c: v })} />
      </>
    );
  }
  const kind = sec.type || "rect";
  const hasOri = Array.isArray(sec.orientation);
  return (
    <>
      <Select label="Section" value={kind} onChange={(t) => onChange({ ...BEAM_DEFAULTS[t], ...(hasOri ? { orientation: sec.orientation } : {}) })}
        options={BEAM_TYPES} />
      {kind === "rect" && (
        <>
          <Num label="Width b (local y)" unit="m" value={sec.b} min={1e-9} onCommit={(v) => set({ b: v })} />
          <Num label="Height h (local z, deep side)" unit="m" value={sec.h} min={1e-9} onCommit={(v) => set({ h: v })} />
        </>
      )}
      {kind === "circle" && <Num label="Diameter d" unit="m" value={sec.d} min={1e-9} onCommit={(v) => set({ d: v })} />}
      {kind === "pipe" && (
        <>
          <Num label="Outer diameter d" unit="m" value={sec.d} min={1e-9} onCommit={(v) => set({ d: v })} />
          <Num label="Wall thickness t" unit="m" value={sec.t} min={1e-9} onCommit={(v) => set({ t: v })} />
        </>
      )}
      {kind === "general" && (
        <>
          <Num label="Area A" unit="m²" value={sec.A} min={1e-12} onCommit={(v) => set({ A: v })} />
          <Num label="Iy (bending about local y)" unit="m⁴" value={sec.Iy} min={1e-18} onCommit={(v) => set({ Iy: v })} />
          <Num label="Iz (bending about local z)" unit="m⁴" value={sec.Iz} min={1e-18} onCommit={(v) => set({ Iz: v })} />
          <Num label="Torsion constant J" unit="m⁴" value={sec.J} min={1e-18} onCommit={(v) => set({ J: v })} />
        </>
      )}
      <Check label="Set the section orientation" checked={hasOri}
        onChange={(on) => { const { orientation, ...rest } = sec; onChange(on ? { ...rest, orientation: [0, 0, 1] } : rest); }} />
      {hasOri && (
        <>
          <Vec3 label="Local y direction (approx.)" value={sec.orientation} onChange={(o) => set({ orientation: o })} />
          <p className="hint">The beam axis runs from node 1 to node 2; local z = axis × this direction. By default local y is global Y (global Z for beams along Y).</p>
        </>
      )}
    </>
  );
}
