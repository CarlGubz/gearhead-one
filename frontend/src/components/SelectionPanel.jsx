import React, { useMemo, useState } from "react";
import { Num, Select } from "./fields.jsx";
import { boundingBox, facesInRange, nodesInRange, parseIdList, surfaceNodeIds } from "./utils.js";

/**
 * Selection tools (PrePoMax-style): all / clear / invert, by coordinate range, by face angle, by id list,
 * and named sets. The selection itself lives in the workspace; this panel only edits it.
 */
export default function SelectionPanel({
  nodes, faces, selectedNodes, selectedFaces, setSelectedNodes, setSelectedFaces,
  angle, setAngle, sets, onSaveSet, onDeleteSet,
}) {
  const bb = useMemo(() => (nodes?.length ? boundingBox(nodes) : null), [nodes]);
  const [axis, setAxis] = useState("0");
  const [lo, setLo] = useState(null);
  const [hi, setHi] = useState(null);
  const [ids, setIds] = useState("");
  const [name, setName] = useState("");
  if (!bb) return <p className="hint">Generate or import a mesh to use selection tools.</p>;

  const ax = parseInt(axis, 10);
  const a0 = lo ?? bb.lo[ax];
  const a1 = hi ?? bb.hi[ax];
  const tol = Math.max(bb.size[ax], 1e-9) * 1e-6;
  const surf = () => surfaceNodeIds(faces);
  const hasSel = selectedNodes.length > 0 || selectedFaces.length > 0;

  return (
    <>
      <p className="hint">
        {selectedNodes.length} node(s) · {selectedFaces.length} face(s) selected. With the node tool, click a node (the cursor snaps
        to it; on quadratic meshes also to the mid-side node at the centre of an edge). With the face tool, click the highlighted face.
        Shift adds or removes; the face-angle tool selects a connected face group by angle.
      </p>
      <div className="btn-row">
        <button onClick={() => { setSelectedNodes(surf()); setSelectedFaces([]); }}>All nodes</button>
        <button onClick={() => { setSelectedFaces(faces); setSelectedNodes([]); }}>All faces</button>
        <button onClick={() => {
          const s = new Set(selectedNodes);
          setSelectedNodes(surf().filter((n) => !s.has(n)));
        }} disabled={!selectedNodes.length}>Invert</button>
        <button onClick={() => { setSelectedNodes([]); setSelectedFaces([]); }} disabled={!hasSel}>Clear</button>
      </div>

      <h4>By coordinate</h4>
      <Select label="Axis" value={axis} onChange={(v) => { setAxis(v); setLo(null); setHi(null); }}
        options={[{ value: "0", label: "X" }, { value: "1", label: "Y" }, { value: "2", label: "Z" }]} />
      <div className="vec3">
        <Num value={Number(a0.toPrecision(6))} onCommit={setLo} />
        <Num value={Number(a1.toPrecision(6))} onCommit={setHi} />
      </div>
      <div className="btn-row">
        <button onClick={() => { setSelectedNodes(nodesInRange(nodes, faces, ax, a0 - tol, a1 + tol)); setSelectedFaces([]); }}>Select nodes</button>
        <button onClick={() => { setSelectedFaces(facesInRange(nodes, faces, ax, a0 - tol, a1 + tol)); setSelectedNodes([]); }}>Select faces</button>
      </div>
      <p className="hint">Tip: set both values to the minimum to select a whole end face.</p>

      <h4>By angle</h4>
      <Num label="Max angle between neighbouring faces" unit="°" value={angle} min={0} max={180} onCommit={setAngle} />
      <p className="hint">Use the face-angle tool in the viewport toolbar, then click a face: the connected faces within this angle are selected.</p>

      <h4>By node id</h4>
      <input type="text" value={ids} placeholder="e.g. 3, 8-12, 40" onChange={(e) => setIds(e.target.value)} />
      <button onClick={() => { setSelectedNodes(parseIdList(ids).filter((i) => i < nodes.length)); setSelectedFaces([]); }}>Select ids</button>

      <h4>Named sets</h4>
      <div className="field-row">
        <input type="text" value={name} placeholder="Set name" onChange={(e) => setName(e.target.value)} />
        <button disabled={!hasSel || !name.trim()}
          onClick={() => {
            onSaveSet(name.trim(), selectedFaces.length ? "faces" : "nodes",
              selectedFaces.length ? selectedFaces : selectedNodes);
            setName("");
          }}>Save selection as set</button>
      </div>
      <div className="list-block">
        <ul>
          {(sets || []).map((s) => (
            <li key={s.id}>
              <span>{s.name} <em className="hint">({s.kind}, {s.ids.length})</em></span>
              <span>
                <button className="link" style={{ color: "var(--ws-accent)" }}
                  onClick={() => (s.kind === "faces" ? (setSelectedFaces(s.ids), setSelectedNodes([])) : (setSelectedNodes(s.ids), setSelectedFaces([])))}>use</button>
                <button className="link" onClick={() => onDeleteSet(s.id)}>✕</button>
              </span>
            </li>
          ))}
          {!(sets || []).length && <p className="hint">No sets yet.</p>}
        </ul>
      </div>
    </>
  );
}
