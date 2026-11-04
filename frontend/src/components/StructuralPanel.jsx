import React, { useState } from "react";
import { Num, Select } from "./fields.jsx";
import SectionEditor, { DEFAULT_SECTIONS } from "./SectionEditor.jsx";
import { STRUCT_ELEMENTS } from "./utils.js";

const SHAPES = {
  line: { label: "Straight line", types: ["B31", "T3D2", "SPRING2"],
    dims: { x1: 0, y1: 0, z1: 0, x2: 1, y2: 0, z2: 0 } },
  portal: { label: "Portal frame (x–z plane)", types: ["B31", "T3D2"], dims: { width: 2, height: 1.5 } },
  plate: { label: "Flat plate (x–y plane)", types: ["S4"], dims: { length: 1, width: 1 } },
  cylinder: { label: "Cylindrical shell (axis z)", types: ["S4"], dims: { radius: 0.5, length: 2, angle: 360 } },
};

function parseRows(text, width, label) {
  const rows = String(text || "").split(/\n+/).map((l) => l.trim()).filter(Boolean)
    .map((l) => l.split(/[\s,;]+/).filter(Boolean).map(Number));
  if (!rows.length) throw new Error(`Enter at least one ${label}.`);
  rows.forEach((r, i) => {
    if (r.length !== width || r.some((v) => !Number.isFinite(v))) throw new Error(`${label} ${i + 1} needs ${width} numbers.`);
  });
  return rows;
}

/** Beams, trusses, springs and shells: generate, add members, or define a layout by coordinates. */
export default function StructuralPanel({ project, hasMesh, onGenerate, onAppend, onCustom, onMessage }) {
  const current = project?.element_type;
  const [etype, setEtype] = useState(STRUCT_ELEMENTS[current] ? current : "B31");
  const [shape, setShape] = useState("line");
  const [dims, setDims] = useState(SHAPES.line.dims);
  const [divisions, setDivisions] = useState(4);
  const [section, setSection] = useState(DEFAULT_SECTIONS.B31);
  const [offset, setOffset] = useState([0, 0, 0]);
  const [nodeText, setNodeText] = useState("0 0 0\n1 0 0\n0.5 0.8 0");
  const [elemText, setElemText] = useState("0 2\n1 2");
  const [open, setOpen] = useState(false);

  const changeType = (t) => {
    setEtype(t);
    setSection(DEFAULT_SECTIONS[t]);
    if (!SHAPES[shape].types.includes(t)) {
      const s = Object.keys(SHAPES).find((k) => SHAPES[k].types.includes(t));
      setShape(s);
      setDims(SHAPES[s].dims);
    }
  };
  const changeShape = (s) => { setShape(s); setDims(SHAPES[s].dims); };
  const shapes = Object.entries(SHAPES).filter(([, v]) => v.types.includes(etype));
  const payload = () => ({ kind: shape, dims, divisions, element_type: etype, section });
  const sameType = current === etype;

  const custom = () => {
    try {
      const nodes = parseRows(nodeText, 3, "node");
      const raw = parseRows(elemText, etype === "S4" ? 4 : 2, "element");
      onCustom({ nodes, elements: raw, element_type: etype, section });
    } catch (e) {
      onMessage(e.message, "warn");
    }
  };

  return (
    <>
      <Select label="Element type" value={etype} onChange={changeType}
        options={Object.entries(STRUCT_ELEMENTS).map(([k, v]) => ({ value: k, label: v.label }))} />
      <Select label="Shape" value={shape} onChange={changeShape} options={shapes.map(([k, v]) => ({ value: k, label: v.label }))} />
      {Object.keys(dims).map((k) => (
        <Num key={k} label={k.replace(/_/g, " ")} unit={k === "angle" ? "deg" : "m"} value={dims[k]}
          min={k === "angle" ? 1 : undefined} max={k === "angle" ? 360 : undefined}
          onCommit={(v) => setDims({ ...dims, [k]: v })} />
      ))}
      <div className="field-row">
        <label>Elements along the shape: {divisions}</label>
        <input type="range" min="1" max="40" value={divisions} onChange={(e) => setDivisions(parseInt(e.target.value, 10))} />
      </div>
      <h4>Section of the new part</h4>
      <SectionEditor etype={etype} section={section} onChange={setSection} />
      <button className="primary" onClick={() => onGenerate(payload())}>Generate structural model</button>
      <p className="hint">Replaces the current model. Structural models use 6 DOFs per node; ties, contact and thermal analyses are for solid models.</p>

      <h4>Add members</h4>
      <button disabled={!hasMesh || !sameType} onClick={() => onAppend({ ...payload(), offset })}
        title="Adds the shape as a new part with its own section; nodes that coincide with existing nodes are joined.">
        + Add as new part
      </button>
      <div className="vec3">
        {["x", "y", "z"].map((a, i) => (
          <Num key={a} value={offset[i]} onCommit={(v) => setOffset(offset.map((x, k) => (k === i ? v : x)))} />
        ))}
      </div>
      <p className="hint">{sameType || !hasMesh ? "Offset (x, y, z) in metres; coincident nodes join the new members to the existing ones." : `The model uses ${current} elements — pick the same type to add members.`}</p>

      <button className="toolbar-btn" style={{ width: "auto", padding: "0 10px" }} onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} Define by coordinates
      </button>
      {open && (
        <>
          <div className="field-row">
            <label>Nodes (x y z per line)</label>
            <textarea rows={5} value={nodeText} onChange={(e) => setNodeText(e.target.value)} spellCheck={false} />
          </div>
          <div className="field-row">
            <label>Elements ({etype === "S4" ? "4" : "2"} node numbers per line, counting from 0)</label>
            <textarea rows={5} value={elemText} onChange={(e) => setElemText(e.target.value)} spellCheck={false} />
          </div>
          <button onClick={custom}>Create from lists</button>
        </>
      )}
    </>
  );
}
