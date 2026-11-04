import React, { useMemo, useState } from "react";
import { Check, Num, Select, Vec3 } from "./fields.jsx";

const MECH = ["static", "nonlinear", "modal", "buckling", "dynamic", "thermomech"];
const THERMAL = ["thermal", "thermal_transient", "thermomech"];

// id, label, group, family, what the selection must contain
const KINDS = [
  { id: "fixed", label: "Support · fixed (all directions)", group: "bc", fam: "mech", needs: "nodes" },
  { id: "symmetry", label: "Support · symmetry plane", group: "bc", fam: "mech", needs: "nodes" },
  { id: "displacement", label: "Support · prescribed displacement", group: "bc", fam: "mech", needs: "nodes" },
  { id: "ref_bc", label: "Support · reference point", group: "bc", fam: "mech", needs: "ref" },
  { id: "force", label: "Load · force on nodes", group: "load", fam: "mech", needs: "nodes", noModal: true },
  { id: "pressure", label: "Load · pressure on faces", group: "load", fam: "mech", needs: "faces", noModal: true },
  { id: "traction", label: "Load · traction vector on faces", group: "load", fam: "mech", needs: "faces", noModal: true },
  { id: "ref_force", label: "Load · remote force / moment (reference point)", group: "load", fam: "mech", needs: "ref", noModal: true },
  { id: "temperature", label: "Thermal · fixed temperature", group: "bc", fam: "thermal", needs: "nodes" },
  { id: "heat_flux", label: "Thermal · heat source on nodes (W)", group: "load", fam: "thermal", needs: "nodes" },
  { id: "surface_flux", label: "Thermal · surface heat flux (W/m²)", group: "load", fam: "thermal", needs: "faces" },
  { id: "body_heat", label: "Thermal · volumetric heat (W/m³)", group: "load", fam: "thermal", needs: "none" },
  { id: "convection", label: "Thermal · convection film", group: "load", fam: "thermal", needs: "faces" },
  { id: "radiation", label: "Thermal · radiation to ambient", group: "load", fam: "thermal", needs: "faces" },
];

// Structural models (beams, trusses, springs, shells): 6 DOFs per node
const SKINDS = [
  { id: "s_fixed", label: "Support · fixed / clamped (all 6 DOFs)", group: "bc", needs: "nodes" },
  { id: "s_pinned", label: "Support · pinned (3 translations)", group: "bc", needs: "nodes" },
  { id: "s_custom", label: "Support · chosen DOFs / prescribed values", group: "bc", needs: "nodes" },
  { id: "s_force", label: "Load · force and moment on nodes", group: "load", needs: "nodes", noModal: true },
  { id: "s_pressure", label: "Load · pressure on shell faces", group: "load", needs: "faces", noModal: true, only: ["S4"] },
  { id: "s_traction", label: "Load · traction vector on shell faces", group: "load", needs: "faces", noModal: true, only: ["S4"] },
  { id: "s_line", label: "Load · uniform line load on beams", group: "load", needs: "none", noModal: true, only: ["B31"] },
  { id: "s_mass", label: "Mass · point mass on nodes (kg)", group: "load", needs: "nodes" },
];

const AXES = ["X", "Y", "Z"];
const DOF6 = ["x", "y", "z", "rx", "ry", "rz"];

function describe(item) {
  if (item.bc_type === "temperature") return `${item.node_ids.length} node(s), T = ${item.values?.[0]} °C`;
  if (item.ref_id) return `reference point, dofs ${item.dofs.map((d) => ["x", "y", "z", "rx", "ry", "rz"][d]).join(",")}`;
  if (item.dofs) return `${item.node_ids.length} node(s), dofs ${item.dofs.map((d) => DOF6[d]).join(",")}${item.values?.some((v) => v) ? ` = ${item.values.join(", ")}` : ""}`;
  const k = item.load_type;
  if (k === "line_load") return `q = (${item.vector.join(", ")}) N/m${item.part_id ? " on one part" : ""}`;
  if (k === "point_mass") return `${item.node_ids.length} node(s), m = ${item.magnitude} kg`;
  if (k === "force" && item.moment?.some((v) => v)) return `${item.node_ids.length} node(s), F = (${item.vector.join(", ")}) N, M = (${item.moment.join(", ")}) N·m`;
  const f = (item.faces || []).length;
  if (k === "force") return `${item.node_ids.length} node(s), F = (${item.vector.map((v) => +v.toFixed(3)).join(", ")}) N`;
  if (k === "pressure") return `${f} face(s), p = ${item.magnitude} Pa`;
  if (k === "traction") return `${f} face(s), t = (${item.vector.join(", ")}) Pa`;
  if (k === "ref_force") return `F = (${item.vector.join(", ")}) N, M = (${item.moment.join(", ")}) N·m`;
  if (k === "heat_flux") return `${item.node_ids.length} node(s), Q = ${item.magnitude} W`;
  if (k === "surface_flux") return `${f} face(s), q = ${item.magnitude} W/m²`;
  if (k === "body_heat") return `q = ${item.magnitude} W/m³`;
  if (k === "convection") return `${f} face(s), h = ${item.h}, T∞ = ${item.T_inf} °C`;
  if (k === "radiation") return `${f} face(s), ε = ${item.emissivity}, T∞ = ${item.T_inf} °C`;
  return "";
}

export default function BCLoadPanel({
  analysisType, selectedNodes, selectedFaces, boundaryConditions, loads, refPoints,
  onAddBC, onDeleteBC, onAddLoad, onDeleteLoad, onMessage, structuralType, parts,
}) {
  const kinds = useMemo(
    () => (structuralType
      ? SKINDS.filter((k) => (!k.only || k.only.includes(structuralType)) && !(k.noModal && analysisType === "modal"))
      : KINDS.filter((k) => (k.fam === "mech" ? MECH : THERMAL).includes(analysisType) && !(k.noModal && analysisType === "modal"))),
    [analysisType, structuralType]
  );
  const [partId, setPartId] = useState("");
  const [kind, setKind] = useState(kinds[0].id);
  const active = kinds.find((k) => k.id === kind) || kinds[0];

  const [axis, setAxis] = useState("0");
  const [dofOn, setDofOn] = useState([true, false, false, false, false, false]);
  const [dofVal, setDofVal] = useState([0, 0, 0, 0, 0, 0]);
  const [vec, setVec] = useState([0, 0, -1000]);
  const [mom, setMom] = useState([0, 0, 0]);
  const [mag, setMag] = useState(1000);
  const [temp, setTemp] = useState(100);
  const [h, setH] = useState(25);
  const [tinf, setTinf] = useState(20);
  const [eps, setEps] = useState(0.8);
  const [refId, setRefId] = useState("");

  const refs = refPoints || [];
  const needText = { nodes: "Select nodes first (node tool in the viewport toolbar, or the Selection panel).", faces: "Select faces first (face tool in the viewport toolbar).", ref: "Create a reference point first (Constraints panel).", none: "" }[active.needs];
  const ready =
    active.needs === "none" ||
    (active.needs === "nodes" && (selectedNodes.length > 0 || selectedFaces.length > 0)) ||
    (active.needs === "faces" && selectedFaces.length > 0) ||
    (active.needs === "ref" && refs.length > 0);
  const nodeIds = selectedNodes.length ? selectedNodes : Array.from(new Set((selectedFaces || []).flat()));
  const rid = refId || refs[0]?.id;

  const add = () => {
    switch (active.id) {
      case "s_fixed": return onAddBC({ name: "Fixed", bc_type: "displacement", node_ids: nodeIds, dofs: [0, 1, 2, 3, 4, 5], values: [0, 0, 0, 0, 0, 0] });
      case "s_pinned": return onAddBC({ name: "Pinned", bc_type: "displacement", node_ids: nodeIds, dofs: [0, 1, 2], values: [0, 0, 0] });
      case "s_custom": {
        const dofs = [0, 1, 2, 3, 4, 5].filter((i) => dofOn[i]);
        if (!dofs.length) return onMessage("Tick at least one degree of freedom.", "warn");
        return onAddBC({ name: "Support", bc_type: "displacement", node_ids: nodeIds, dofs, values: dofs.map((i) => dofVal[i]) });
      }
      case "s_force": return onAddLoad({ name: "Force", load_type: "force", node_ids: nodeIds, vector: vec, moment: mom });
      case "s_pressure": return onAddLoad({ name: "Pressure", load_type: "pressure", faces: selectedFaces, magnitude: mag });
      case "s_traction": return onAddLoad({ name: "Traction", load_type: "traction", faces: selectedFaces, vector: vec });
      case "s_line": return onAddLoad({ name: "Line load", load_type: "line_load", vector: vec, ...(partId ? { part_id: partId } : {}) });
      case "s_mass": return onAddLoad({ name: "Point mass", load_type: "point_mass", node_ids: nodeIds, magnitude: mag });
      case "fixed": return onAddBC({ name: "Fixed", bc_type: "displacement", node_ids: nodeIds, dofs: [0, 1, 2], values: [0, 0, 0] });
      case "symmetry": return onAddBC({ name: `Symmetry ${AXES[+axis]}`, bc_type: "displacement", node_ids: nodeIds, dofs: [+axis], values: [0] });
      case "displacement": {
        const dofs = [0, 1, 2].filter((i) => dofOn[i]);
        if (!dofs.length) return onMessage("Tick at least one direction.", "warn");
        return onAddBC({ name: "Displacement", bc_type: "displacement", node_ids: nodeIds, dofs, values: dofs.map((i) => dofVal[i]) });
      }
      case "ref_bc": {
        const dofs = [0, 1, 2, 3, 4, 5].filter((i) => dofOn[i]);
        if (!dofs.length) return onMessage("Tick at least one degree of freedom.", "warn");
        return onAddBC({ name: "Reference point BC", bc_type: "displacement", ref_id: rid, node_ids: [], dofs, values: dofs.map((i) => dofVal[i]) });
      }
      case "temperature": return onAddBC({ name: "Fixed temperature", bc_type: "temperature", node_ids: nodeIds, dofs: [0], values: [temp] });
      case "force": return onAddLoad({ name: "Force", load_type: "force", node_ids: nodeIds, vector: vec });
      case "pressure": return onAddLoad({ name: "Pressure", load_type: "pressure", faces: selectedFaces, magnitude: mag });
      case "traction": return onAddLoad({ name: "Traction", load_type: "traction", faces: selectedFaces, vector: vec });
      case "ref_force": return onAddLoad({ name: "Remote load", load_type: "ref_force", ref_id: rid, vector: vec, moment: mom });
      case "heat_flux": return onAddLoad({ name: "Heat source", load_type: "heat_flux", node_ids: nodeIds, magnitude: mag });
      case "surface_flux": return onAddLoad({ name: "Surface flux", load_type: "surface_flux", faces: selectedFaces, magnitude: mag });
      case "body_heat": return onAddLoad({ name: "Body heat", load_type: "body_heat", magnitude: mag });
      case "convection": return onAddLoad({ name: "Convection", load_type: "convection", faces: selectedFaces, h, T_inf: tinf });
      case "radiation": return onAddLoad({ name: "Radiation", load_type: "radiation", faces: selectedFaces, emissivity: eps, T_inf: tinf });
      default: return null;
    }
  };

  const dofLabels = active.id === "ref_bc" || active.id === "s_custom" ? ["ux", "uy", "uz", "rx", "ry", "rz"] : ["X", "Y", "Z"];

  return (
    <>
      <p className="hint">
        {selectedNodes.length} node(s) / {selectedFaces.length} face(s) selected
      </p>
      <Select label="Add" value={active.id} onChange={setKind} options={kinds.map((k) => ({ value: k.id, label: k.label }))} />

      {active.id === "symmetry" && (
        <Select label="Plane normal" value={axis} onChange={setAxis}
          options={[{ value: "0", label: "X (fixes ux)" }, { value: "1", label: "Y (fixes uy)" }, { value: "2", label: "Z (fixes uz)" }]} />
      )}
      {(active.id === "displacement" || active.id === "ref_bc" || active.id === "s_custom") && (
        <div className="dof-grid">
          {dofLabels.map((l, i) => (
            <div key={l}>
              <Check label={l} checked={dofOn[i]} onChange={(on) => setDofOn(dofOn.map((x, k) => (k === i ? on : x)))} />
              <Num value={dofVal[i]} disabled={!dofOn[i]} onCommit={(v) => setDofVal(dofVal.map((x, k) => (k === i ? v : x)))} />
            </div>
          ))}
        </div>
      )}
      {(active.id === "ref_bc" || active.id === "ref_force") && (
        <Select label="Reference point" value={rid || ""} onChange={setRefId}
          options={refs.length ? refs.map((r) => ({ value: r.id, label: r.name })) : [{ value: "", label: "(none)" }]} />
      )}
      {["force", "traction", "ref_force", "s_force", "s_traction", "s_line"].includes(active.id) && (
        <Vec3 label={active.id === "traction" || active.id === "s_traction" ? "Traction" : active.id === "s_line" ? "Load per length" : "Force"}
          unit={active.id === "traction" || active.id === "s_traction" ? "Pa" : active.id === "s_line" ? "N/m" : "N"} value={vec} onChange={setVec} />
      )}
      {(active.id === "ref_force" || active.id === "s_force") && <Vec3 label="Moment" unit="N·m" value={mom} onChange={setMom} />}
      {active.id === "s_line" && (
        <Select label="Applies to" value={partId} onChange={setPartId}
          options={[{ value: "", label: "All beams" }, ...(parts || []).map((p) => ({ value: p.id, label: p.name }))]} />
      )}
      {active.id === "s_pressure" && (
        <>
          <Num label="Pressure" unit="Pa" value={mag} onCommit={setMag} />
          <p className="hint">Positive pressure pushes against the element normal (right-hand rule of the node order). Plates point +Z, cylinders outward.</p>
        </>
      )}
      {active.id === "s_mass" && <Num label="Mass at each selected node" unit="kg" value={mag} min={1e-12} onCommit={setMag} />}
      {active.id === "pressure" && <Num label="Pressure (+ = into the surface)" unit="Pa" value={mag} onCommit={setMag} />}
      {active.id === "heat_flux" && <Num label="Heat rate Q" unit="W" value={mag} onCommit={setMag} />}
      {active.id === "surface_flux" && <Num label="Flux (+ = into the body)" unit="W/m²" value={mag} onCommit={setMag} />}
      {active.id === "body_heat" && <Num label="Volumetric heat" unit="W/m³" value={mag} onCommit={setMag} />}
      {active.id === "temperature" && <Num label="Temperature" unit="°C" value={temp} onCommit={setTemp} />}
      {active.id === "convection" && <Num label="Film coefficient h" unit="W/(m²·K)" value={h} min={0} onCommit={setH} />}
      {active.id === "radiation" && <Num label="Emissivity ε" value={eps} min={0} max={1} onCommit={setEps} />}
      {(active.id === "convection" || active.id === "radiation") && <Num label="Ambient temperature T∞" unit="°C" value={tinf} onCommit={setTinf} />}

      <button className="primary" disabled={!ready} onClick={add}>Add</button>
      {!ready && needText && <p className="hint">{needText}</p>}

      <div className="list-block">
        <h4>Supports &amp; temperatures</h4>
        {(boundaryConditions || []).length === 0 && <p className="hint">None yet.</p>}
        <ul>
          {(boundaryConditions || []).map((bc) => (
            <li key={bc.id}>
              <span><strong>{bc.name}</strong> — {describe(bc)}</span>
              <button className="link" onClick={() => onDeleteBC(bc.id)}>✕</button>
            </li>
          ))}
        </ul>
      </div>
      {(analysisType !== "modal" || structuralType) && (
        <div className="list-block">
          <h4>Loads</h4>
          {(loads || []).length === 0 && <p className="hint">None yet.</p>}
          <ul>
            {(loads || []).map((ld) => (
              <li key={ld.id}>
                <span><strong>{ld.name}</strong> — {describe(ld)}</span>
                <button className="link" onClick={() => onDeleteLoad(ld.id)}>✕</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
