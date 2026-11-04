import React, { useState } from "react";
import { Num, Select, Vec3 } from "./fields.jsx";

/** Reference points and rigid-body / tie / contact constraints. */
export default function ConstraintPanel({
  nodes, selectedNodes, selectedFaces, refPoints, constraints, sets, parts,
  onAddRef, onDeleteRef, onAddConstraint, onDeleteConstraint, onMessage,
}) {
  const [coords, setCoords] = useState([0, 0, 0]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("tie_parts");
  const [refId, setRefId] = useState("");
  const [masterSet, setMasterSet] = useState("");
  const [masterPart, setMasterPart] = useState("");
  const [slavePart, setSlavePart] = useState("");
  const [tol, setTol] = useState(0);
  const [penalty, setPenalty] = useState(10);

  const refs = refPoints || [];
  const plist = parts || [];
  const rid = refId || refs[0]?.id;
  const faceSets = (sets || []).filter((s) => s.kind === "faces");
  const mp = masterPart || plist[0]?.id || "";
  const sp = slavePart || plist.find((p) => p.id !== mp)?.id || "";
  const partName = (id) => plist.find((p) => p.id === id)?.name || "?";
  const centroid = () => {
    const ids = selectedNodes.length ? selectedNodes : Array.from(new Set(selectedFaces.flat()));
    if (!ids.length) return null;
    const c = [0, 0, 0];
    ids.forEach((i) => nodes[i].forEach((v, k) => { c[k] += v / ids.length; }));
    return c.map((v) => +v.toPrecision(8));
  };

  const addRigid = () => {
    if (!rid) return onMessage("Create a reference point first.", "warn");
    if (!selectedFaces.length && !selectedNodes.length) return onMessage("Select the surface that should move rigidly with the reference point.", "warn");
    onAddConstraint(selectedFaces.length
      ? { name: "Rigid body", type: "rigid", ref_id: rid, faces: selectedFaces }
      : { name: "Rigid body", type: "rigid", ref_id: rid, node_ids: selectedNodes });
  };
  const addTieParts = () => {
    if (plist.length < 2) return onMessage("Add a second part first (Geometry & Mesh → Add as separate body).", "warn");
    if (!mp || !sp || mp === sp) return onMessage("Choose two different parts.", "warn");
    onAddConstraint({
      name: `Tie ${partName(sp)} → ${partName(mp)}`, type: "tie", master_part: mp, slave_part: sp,
      ...(tol > 0 ? { tolerance: tol } : {}),
    });
  };
  const addContact = () => {
    if (plist.length < 2) return onMessage("Add a second part first (Geometry & Mesh → Add as separate body).", "warn");
    if (!mp || !sp || mp === sp) return onMessage("Choose two different parts.", "warn");
    onAddConstraint({
      name: `Contact ${partName(sp)} → ${partName(mp)}`, type: "contact", master_part: mp, slave_part: sp, penalty_factor: penalty,
    });
  };
  const addTie = () => {
    const master = faceSets.find((s) => s.id === masterSet) || faceSets[0];
    if (!master) return onMessage("Save the master surface as a face set first (Selection panel).", "warn");
    if (!selectedFaces.length && !selectedNodes.length) return onMessage("Select the slave surface/nodes first.", "warn");
    onAddConstraint({
      name: "Tie", type: "tie", master_faces: master.ids,
      ...(selectedFaces.length ? { slave_faces: selectedFaces } : { slave_node_ids: selectedNodes }),
    });
  };

  return (
    <>
      <h4>Reference points</h4>
      <input type="text" value={name} placeholder="Name (optional)" onChange={(e) => setName(e.target.value)} />
      <Vec3 label="Coordinates" unit="m" value={coords} onChange={setCoords} />
      <div className="btn-row">
        <button onClick={() => onAddRef({ name: name.trim() || `RP${refs.length + 1}`, coords })}>Add</button>
        <button onClick={() => { const c = centroid(); if (!c) return onMessage("Select nodes or faces first.", "warn"); setCoords(c); }}
          title="Fill the coordinates with the centroid of the current selection">Use selection centroid</button>
      </div>
      <div className="list-block">
        <ul>
          {refs.map((r) => (
            <li key={r.id}>
              <span>{r.name} <em className="hint">({r.coords.map((v) => +v.toPrecision(4)).join(", ")})</em></span>
              <button className="link" onClick={() => onDeleteRef(r.id)}>✕</button>
            </li>
          ))}
          {!refs.length && <p className="hint">None yet.</p>}
        </ul>
      </div>

      <h4>Constraints</h4>
      <Select value={kind} onChange={setKind} options={[
        { value: "tie_parts", label: "Tie between parts (automatic)" },
        { value: "contact", label: "Contact between parts (frictionless)" },
        { value: "rigid", label: "Rigid body (surface ↔ reference point)" },
        { value: "tie", label: "Tie from a selection (manual)" },
      ]} />
      {kind === "tie_parts" && (
        <>
          <Select label="Master part (recommended: the finer or stiffer one)" value={mp} onChange={setMasterPart}
            options={plist.map((p) => ({ value: p.id, label: p.name }))} />
          <Select label="Slave part" value={sp} onChange={setSlavePart}
            options={plist.filter((p) => p.id !== mp).map((p) => ({ value: p.id, label: p.name }))} />
          <Num label="Gap tolerance (0 = automatic)" unit="m" value={tol} min={0} onCommit={setTol}
            hint="Slave nodes within this distance of the master surface are tied; the rest are left free. Matching meshes tie exactly; non-matching ones are first-order accurate." />
          <button className="primary" onClick={addTieParts}>Add tie</button>
        </>
      )}
      {kind === "contact" && (
        <>
          <Select label="Master part (recommended: the stiffer or coarser one)" value={mp} onChange={setMasterPart}
            options={plist.map((p) => ({ value: p.id, label: p.name }))} />
          <Select label="Slave part" value={sp} onChange={setSlavePart}
            options={plist.filter((p) => p.id !== mp).map((p) => ({ value: p.id, label: p.name }))} />
          <Num label="Penalty factor" value={penalty} min={0.1} onCommit={setPenalty}
            hint="Contact stiffness = factor × E / element size. Higher values reduce penetration but can slow convergence. Use a static or nonlinear analysis; every body needs supports." />
          <button className="primary" onClick={addContact}>Add contact</button>
        </>
      )}
      {kind === "rigid" && (
        <>
          <Select label="Reference point" value={rid || ""} onChange={setRefId}
            options={refs.length ? refs.map((r) => ({ value: r.id, label: r.name })) : [{ value: "", label: "(none)" }]} />
          <p className="hint">The selected surface moves as a rigid body with the reference point. Then apply loads or supports to the reference point.</p>
          <button className="primary" onClick={addRigid}>Add rigid body</button>
        </>
      )}
      {kind === "tie" && (
        <>
          <Select label="Master surface (face set)" value={masterSet || faceSets[0]?.id || ""} onChange={setMasterSet}
            options={faceSets.length ? faceSets.map((s) => ({ value: s.id, label: s.name })) : [{ value: "", label: "(save a face set first)" }]} />
          <p className="hint">The current selection is the slave side. Slave nodes follow the master surface by interpolation.</p>
          <button className="primary" onClick={addTie}>Add tie</button>
        </>
      )}
      <div className="list-block">
        <ul>
          {(constraints || []).map((c) => (
            <li key={c.id}>
              <span><strong>{c.name}</strong> — {c.type === "rigid" ? `rigid → ${refs.find((r) => r.id === c.ref_id)?.name || "?"}`
                : c.type === "contact" ? `contact ${partName(c.master_part)} ↔ ${partName(c.slave_part)}`
                : c.master_part ? `tie ${partName(c.slave_part)} → ${partName(c.master_part)}` : "tie"}</span>
              <button className="link" onClick={() => onDeleteConstraint(c.id)}>✕</button>
            </li>
          ))}
          {!(constraints || []).length && <p className="hint">None yet.</p>}
        </ul>
      </div>
    </>
  );
}
