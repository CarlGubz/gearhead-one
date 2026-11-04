/* Pure helpers for the workspace: selection tools, result display model, number formatting, exports. */

/* ------------------------------------------------------------------ formatting */
export function fmt(v, digits = 4) {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(digits - 1);
  return Number(v.toPrecision(digits)).toString();
}

export function minMax(arr) {
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return arr.length ? [lo, hi] : [0, 1];
}

/* --------------------------------------------------------------- element info */
export const ELEMENTS = {
  C3D4: { label: "Tet4 (linear tetra)", corners: 4, vtk: 10 },
  C3D10: { label: "Tet10 (quadratic tetra)", corners: 4, vtk: 24 },
  C3D8: { label: "Hex8 (linear brick)", corners: 8, vtk: 12 },
  C3D20: { label: "Hex20 (quadratic brick)", corners: 8, vtk: 25 },
};

/** Structural elements (6 DOFs per node): beams, trusses, springs, shells. */
export const STRUCT_ELEMENTS = {
  B31: { label: "Beam (B31, 3-D Timoshenko)", vtk: 3, line: true },
  T3D2: { label: "Truss bar (T3D2)", vtk: 3, line: true },
  SPRING2: { label: "Spring / dashpot (SPRING2)", vtk: 3, line: true },
  S4: { label: "Shell (S4, MITC4 quad)", vtk: 9, line: false },
};
export const isStructural = (t) => !!STRUCT_ELEMENTS[t];
export const isLineElement = (t) => !!STRUCT_ELEMENTS[t]?.line;

/**
 * Beams, bars and springs have no surface, so the viewport draws each as a thin hexagonal tube.
 * The tube's ring vertices are appended after the real nodes; `baseOf[v]` gives the node a vertex belongs to, so
 * displacements, contours and picks keep addressing real node ids.
 */
export function buildTubeView(nodes, elements, partOf, radius, sides = 6) {
  const n = nodes.length;
  const viewNodes = nodes.map((p) => p.slice());
  const baseOf = new Int32Array(n + elements.length * 2 * sides);
  for (let i = 0; i < n; i++) baseOf[i] = i;
  const faces = [];
  const faceParts = [];
  const ringsOf = {};
  elements.forEach((el, e) => {
    const [a, b] = el;
    const pa = nodes[a];
    const pb = nodes[b];
    const d = [pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]];
    const len = Math.hypot(d[0], d[1], d[2]) || 1;
    d[0] /= len; d[1] /= len; d[2] /= len;
    const ref = Math.abs(d[2]) > 0.9 ? [1, 0, 0] : [0, 0, 1];
    let u = [d[1] * ref[2] - d[2] * ref[1], d[2] * ref[0] - d[0] * ref[2], d[0] * ref[1] - d[1] * ref[0]];
    const ul = Math.hypot(u[0], u[1], u[2]) || 1;
    u = u.map((x) => x / ul);
    const v = [d[1] * u[2] - d[2] * u[1], d[2] * u[0] - d[0] * u[2], d[0] * u[1] - d[1] * u[0]];
    const r = Math.min(radius, len * 0.35);
    const base = n + e * 2 * sides;
    [a, b].forEach((node, end) => {
      const c = nodes[node];
      for (let k = 0; k < sides; k++) {
        const th = (2 * Math.PI * k) / sides;
        const idx = base + end * sides + k;
        viewNodes[idx] = [
          c[0] + r * (Math.cos(th) * u[0] + Math.sin(th) * v[0]),
          c[1] + r * (Math.cos(th) * u[1] + Math.sin(th) * v[1]),
          c[2] + r * (Math.cos(th) * u[2] + Math.sin(th) * v[2]),
        ];
        baseOf[idx] = node;
        (ringsOf[node] = ringsOf[node] || []).push(idx);
      }
    });
    for (let k = 0; k < sides; k++) {
      const k1 = (k + 1) % sides;
      faces.push([base + k, base + k1, base + sides + k1], [base + k, base + sides + k1, base + sides + k]);
      faceParts.push(partOf[e] || 0, partOf[e] || 0);
    }
  });
  return { nodes: viewNodes, faces, faceParts, baseOf, ringsOf };
}

/** Nodes that appear on the displayed surface (corner nodes of the render triangles). */
export function surfaceNodeIds(faces) {
  const s = new Set();
  faces.forEach((t) => t.forEach((n) => s.add(n)));
  return Array.from(s);
}

export function boundingBox(nodes) {
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  nodes.forEach((p) => {
    for (let k = 0; k < 3; k++) {
      if (p[k] < lo[k]) lo[k] = p[k];
      if (p[k] > hi[k]) hi[k] = p[k];
    }
  });
  return { lo, hi, size: [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]] };
}

/* ------------------------------------------------------------------ selection */
function triNormal(nodes, t) {
  const a = nodes[t[0]];
  const b = nodes[t[1]];
  const c = nodes[t[2]];
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
  const len = Math.hypot(n[0], n[1], n[2]) || 1;
  return [n[0] / len, n[1] / len, n[2] / len];
}

/** Flood-fill over edge-adjacent triangles whose normals differ by <= maxAngleDeg (PrePoMax "select by angle"). */
export function facesByAngle(faces, nodes, startIndex, maxAngleDeg) {
  const normals = faces.map((t) => triNormal(nodes, t));
  const edgeMap = new Map();
  faces.forEach((t, fi) => {
    for (let k = 0; k < 3; k++) {
      const a = t[k];
      const b = t[(k + 1) % 3];
      const key = a < b ? `${a}_${b}` : `${b}_${a}`;
      if (!edgeMap.has(key)) edgeMap.set(key, []);
      edgeMap.get(key).push(fi);
    }
  });
  const cosLimit = Math.cos((maxAngleDeg * Math.PI) / 180);
  const seen = new Set([startIndex]);
  const stack = [startIndex];
  while (stack.length) {
    const fi = stack.pop();
    const t = faces[fi];
    for (let k = 0; k < 3; k++) {
      const a = t[k];
      const b = t[(k + 1) % 3];
      const key = a < b ? `${a}_${b}` : `${b}_${a}`;
      for (const nb of edgeMap.get(key) || []) {
        if (seen.has(nb)) continue;
        const n1 = normals[fi];
        const n2 = normals[nb];
        if (n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2] >= cosLimit - 1e-9) {
          seen.add(nb);
          stack.push(nb);
        }
      }
    }
  }
  return Array.from(seen).map((i) => faces[i]);
}

/** Surface nodes whose coordinate on `axis` lies in [lo, hi]. */
export function nodesInRange(nodes, faces, axis, lo, hi) {
  return surfaceNodeIds(faces).filter((i) => nodes[i][axis] >= lo && nodes[i][axis] <= hi);
}

/** Surface triangles whose three nodes all lie in the range on `axis`. */
export function facesInRange(nodes, faces, axis, lo, hi) {
  return faces.filter((t) => t.every((i) => nodes[i][axis] >= lo && nodes[i][axis] <= hi));
}

/** Parse "1, 5, 10-20" into a sorted unique id list. */
export function parseIdList(text) {
  const out = new Set();
  String(text || "")
    .split(/[\s,;]+/)
    .filter(Boolean)
    .forEach((part) => {
      const m = part.match(/^(\d+)-(\d+)$/);
      if (m) {
        const [a, b] = [parseInt(m[1], 10), parseInt(m[2], 10)];
        for (let i = Math.min(a, b); i <= Math.max(a, b) && out.size < 100000; i++) out.add(i);
      } else if (/^\d+$/.test(part)) out.add(parseInt(part, 10));
    });
  return Array.from(out).sort((a, b) => a - b);
}

/* ----------------------------------------------------------------- result model */
const AXES = ["x", "y", "z"];

export const FIELD_INFO = {
  displacement_magnitude: ["Displacement magnitude", "m"],
  ux: ["Displacement X", "m"],
  uy: ["Displacement Y", "m"],
  uz: ["Displacement Z", "m"],
  von_mises: ["von Mises stress", "Pa"],
  tresca: ["Tresca stress", "Pa"],
  principal_max: ["Max principal stress S1", "Pa"],
  principal_mid: ["Mid principal stress S2", "Pa"],
  principal_min: ["Min principal stress S3", "Pa"],
  sxx: ["Stress XX", "Pa"], syy: ["Stress YY", "Pa"], szz: ["Stress ZZ", "Pa"],
  sxy: ["Stress XY", "Pa"], syz: ["Stress YZ", "Pa"], szx: ["Stress ZX", "Pa"],
  exx: ["Strain XX", ""], eyy: ["Strain YY", ""], ezz: ["Strain ZZ", ""],
  exy: ["Strain XY", ""], eyz: ["Strain YZ", ""], ezx: ["Strain ZX", ""],
  equivalent_strain: ["Equivalent strain", ""],
  plastic_strain: ["Equivalent plastic strain", ""],
  strain_energy_density: ["Strain energy density", "J/m³"],
  rotation_magnitude: ["Rotation magnitude", "rad"],
  rx: ["Rotation X", "rad"], ry: ["Rotation Y", "rad"], rz: ["Rotation Z", "rad"],
  axial_force: ["Axial / spring force", "N"],
  shear_force: ["Shear force", "N"],
  torsion: ["Torque", "N·m"],
  bending_moment: ["Bending moment", "N·m"],
  bending_stress: ["Bending stress (extreme fibre)", "Pa"],
  von_mises_top: ["von Mises (shell top)", "Pa"],
  von_mises_bottom: ["von Mises (shell bottom)", "Pa"],
  shell_nx: ["Membrane force Nx", "N/m"], shell_ny: ["Membrane force Ny", "N/m"], shell_nxy: ["Membrane shear Nxy", "N/m"],
  shell_mx: ["Bending moment Mx", "N·m/m"], shell_my: ["Bending moment My", "N·m/m"], shell_mxy: ["Twisting moment Mxy", "N·m/m"],
  shell_shear: ["Transverse shear", "N/m"],
  contact_pressure: ["Contact pressure", "Pa"],
  contact_gap: ["Contact gap (negative = penetration)", "m"],
  temperature: ["Temperature", ""],
  flux_magnitude: ["Heat flux magnitude", "W/m²"],
  flux_x: ["Heat flux X", "W/m²"], flux_y: ["Heat flux Y", "W/m²"], flux_z: ["Heat flux Z", "W/m²"],
};

const FIELD_ORDER = Object.keys(FIELD_INFO);

/** Fields the user can contour for a finished job. */
export function availableFields(results) {
  if (!results) return [];
  const t = results.analysis_type;
  if (t === "modal" || t === "buckling") return [];
  const keys = Object.keys(results.fields || {});
  return FIELD_ORDER.filter((k) => keys.includes(k)).map((k) => ({ key: k, label: FIELD_INFO[k][0], unit: FIELD_INFO[k][1] }));
}

/** False when a run was computed on another mesh (its per-node arrays do not match the current node count). */
export function resultsMatchMesh(results, nNodes) {
  return !Object.values(results?.fields || {}).some((a) => Array.isArray(a) && a.length !== nNodes);
}

export function defaultField(results) {
  const t = results?.analysis_type;
  if (t === "thermal" || t === "thermal_transient") return "temperature";
  if (t === "dynamic") return "displacement_magnitude";
  if (results?.fields && !results.fields.von_mises) return "displacement_magnitude";
  return "von_mises";
}

function expand(ids, arr, n, dim) {
  const out = new Array(n);
  for (let i = 0; i < n; i++) out[i] = dim === 1 ? 0 : [0, 0, 0];
  ids.forEach((id, k) => {
    out[id] = arr[k];
  });
  return out;
}

export function frameCount(results) {
  const t = results?.analysis_type;
  if (t === "modal") return results.frequencies_hz?.length || 0;
  if (t === "buckling") return results.load_factors?.length || 0;
  if (t === "dynamic" || t === "thermal_transient") return results.frames?.length || 0;
  return 0;
}

/**
 * What to draw for the selected field/frame.
 * Returns { field, disp, label, unit } where field is a per-node array (or null) and disp per-node [dx,dy,dz] (or null).
 */
export function buildDisplay(results, sel, nNodes) {
  if (!results) return { field: null, disp: null, label: "", unit: "" };
  const t = results.analysis_type;
  const frame = sel.frame || 0;
  const mag = (arr) => arr.map((d) => Math.hypot(d[0], d[1], d[2]));

  if (t === "modal" || t === "buckling") {
    let shape = results.mode_shapes?.[frame] || results.mode_shapes?.[0];
    if (!shape) return { field: null, disp: null, label: "", unit: "" };
    if (results.mode_shape_nodes) shape = expand(results.mode_shape_nodes, shape, nNodes, 3);
    const head = t === "modal" ? `Mode ${frame + 1} · ${fmt(results.frequencies_hz[frame])} Hz` : `Buckling mode ${frame + 1} · λ = ${fmt(results.load_factors[frame])}`;
    return { field: mag(shape), disp: shape, label: head, unit: "normalised" };
  }
  if (t === "dynamic") {
    let d = results.frames?.[frame];
    if (!d) return { field: null, disp: null, label: "", unit: "" };
    d = expand(results.frame_nodes, d, nNodes, 3);
    const time = results.frame_times?.[frame];
    if (sel.field === "displacement_magnitude" || !results.fields?.[sel.field]) {
      return { field: mag(d), disp: d, label: `Displacement magnitude · t = ${fmt(time)} s`, unit: "m" };
    }
    const [label, unit] = FIELD_INFO[sel.field] || [sel.field, ""];
    return { field: results.fields[sel.field], disp: d, label: `${label} (peak response) · t = ${fmt(time)} s`, unit };
  }
  if (t === "thermal_transient") {
    const fr = results.frames?.[frame];
    const time = results.frame_times?.[frame];
    if (sel.field === "temperature" && fr) {
      return { field: expand(results.frame_nodes, fr, nNodes, 1), disp: null, label: `Temperature · t = ${fmt(time)} s`, unit: "" };
    }
  }
  const field = results.fields?.[sel.field] || null;
  const [label, unit] = FIELD_INFO[sel.field] || [sel.field, ""];
  return { field, disp: results.displacements || null, label, unit };
}

/** Deformation scale that makes the largest displacement ~10% of the model size. */
export function autoScale(disp, size) {
  if (!disp) return 1;
  let mx = 0;
  for (let i = 0; i < disp.length; i++) mx = Math.max(mx, Math.hypot(disp[i][0], disp[i][1], disp[i][2]));
  if (mx <= 0) return 1;
  const raw = (0.1 * Math.max(...size)) / mx;
  return Number(Math.min(1e6, Math.max(1e-3, raw)).toPrecision(2));
}

/* ------------------------------------------------------------------- exports */
export function download(filename, text, mime = "text/plain") {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function resultsToCSV(project, results, disp) {
  const keys = Object.keys(results.fields || {});
  const head = ["node", "x", "y", "z", "ux", "uy", "uz", ...keys.filter((k) => !["ux", "uy", "uz"].includes(k))];
  const lines = [head.join(",")];
  project.nodes.forEach((p, i) => {
    const d = disp?.[i] || [0, 0, 0];
    const row = [i, p[0], p[1], p[2], d[0], d[1], d[2]];
    keys.forEach((k) => {
      if (!["ux", "uy", "uz"].includes(k)) row.push(results.fields[k][i]);
    });
    lines.push(row.join(","));
  });
  return lines.join("\n");
}

/** Legacy ASCII VTK unstructured grid (opens in ParaView). Point data = every nodal field. */
export function resultsToVTK(project, results, disp) {
  const info = ELEMENTS[project.element_type] || STRUCT_ELEMENTS[project.element_type] || ELEMENTS.C3D4;
  const nn = project.elements[0]?.length || 4;
  const L = ["# vtk DataFile Version 3.0", "Gearhead FEA results", "ASCII", "DATASET UNSTRUCTURED_GRID"];
  L.push(`POINTS ${project.nodes.length} double`);
  project.nodes.forEach((p) => L.push(`${p[0]} ${p[1]} ${p[2]}`));
  L.push(`CELLS ${project.elements.length} ${project.elements.length * (nn + 1)}`);
  project.elements.forEach((e) => L.push(`${nn} ${e.join(" ")}`));
  L.push(`CELL_TYPES ${project.elements.length}`);
  project.elements.forEach(() => L.push(String(info.vtk)));
  L.push(`POINT_DATA ${project.nodes.length}`);
  if (disp) {
    L.push("VECTORS displacement double");
    disp.forEach((d) => L.push(`${d[0]} ${d[1]} ${d[2]}`));
  }
  Object.entries(results.fields || {}).forEach(([k, arr]) => {
    if (arr.length !== project.nodes.length) return;
    L.push(`SCALARS ${k} double 1`, "LOOKUP_TABLE default");
    arr.forEach((v) => L.push(String(v)));
  });
  return L.join("\n");
}
