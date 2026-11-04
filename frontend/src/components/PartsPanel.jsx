import React, { useState } from "react";
import { Num } from "./fields.jsx";
import SectionEditor from "./SectionEditor.jsx";
import { PART_COLORS } from "./Viewport3D.jsx";

const css = (c) => `rgb(${c.map((v) => Math.round(v * 255)).join(",")})`;

function PartRow({ part, index, material, materials, hidden, canDelete, onToggle, onUpdate, onMove, onDelete, etype }) {
  const [moving, setMoving] = useState(false);
  const [offset, setOffset] = useState([0, 0, 0]);
  return (
    <li className="part-row">
      <div className="part-head">
        <span className="swatch" style={{ background: css(PART_COLORS[index % PART_COLORS.length]) }} />
        <input type="text" defaultValue={part.name} key={part.name}
          onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== part.name && onUpdate({ name: e.target.value.trim() })}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
        <button className="toolbar-btn" title={hidden ? "Show part" : "Hide part"} onClick={onToggle}>{hidden ? "◌" : "●"}</button>
        <button className="toolbar-btn" title="Move part" onClick={() => setMoving((m) => !m)}>⇄</button>
        <button className="link" disabled={!canDelete} title={canDelete ? "Delete part" : "A model needs at least one part"}
          onClick={() => window.confirm(`Delete part “${part.name}”? Supports, loads and sets that only used it are removed.`) && onDelete()}>✕</button>
      </div>
      <select value={material || "default"} onChange={(e) => onUpdate({ material_id: e.target.value })} title="Material">
        {materials.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
      </select>
      {etype && <SectionEditor etype={etype} section={part.section} onChange={(section) => onUpdate({ section })} />}
      <p className="hint">{part.elem_end - part.elem_start} elements</p>
      {moving && (
        <div className="part-move">
          <div className="vec3">
            {["x", "y", "z"].map((a, i) => (
              <Num key={a} value={offset[i]} onCommit={(v) => setOffset(offset.map((x, k) => (k === i ? v : x)))} />
            ))}
          </div>
          <button onClick={() => { onMove(offset); setMoving(false); setOffset([0, 0, 0]); }}>Move by (m)</button>
        </div>
      )}
    </li>
  );
}

/** Part list: rename, material, show/hide, move, delete. */
export default function PartsPanel({ parts, defaultMaterial, materials, hidden, onToggle, onUpdate, onMove, onDelete, structuralType }) {
  const options = [{ id: "default", name: `${defaultMaterial?.name || "Default"} (default)` }, ...(materials || [])];
  return (
    <>
      {structuralType
        ? <p className="hint">A part is a group of members with one material and one section. Parts that share nodes are connected.</p>
        : <p className="hint">A part is one body. Give each part a material; join parts with a <em>Tie between parts</em> (Constraints panel).</p>}
      <ul className="parts">
        {(parts || []).map((p, i) => (
          <PartRow key={p.id} part={p} index={i} material={p.material_id} materials={options}
            hidden={hidden.has(p.id)} canDelete={parts.length > 1} etype={structuralType}
            onToggle={() => onToggle(p.id)} onUpdate={(patch) => onUpdate(p.id, patch)}
            onMove={(off) => onMove(p.id, off)} onDelete={() => onDelete(p.id)} />
        ))}
      </ul>
    </>
  );
}
