/**
 * Boundary-condition and load glyphs, PrePoMax style: forces and pressures are 3-D arrows pointing the way the
 * load acts, supports are cones that "hold" the node along each restrained axis, temperatures and heat sources
 * are small spheres. Everything here is pure data (positions + directions in model coordinates); the viewport
 * turns it into instanced meshes.
 *
 * A layer is { shape: "arrow" | "cone" | "sphere", color, items: [{ p: [x,y,z], d: [dx,dy,dz] }] }.
 * The tip of an arrow or cone sits on `p` and the glyph extends backwards along `-d`.
 */

export const GLYPH_COLORS = {
  support: "#2563eb", // blue
  force: "#dc2626", // red
  temperature: "#0d9488", // teal
  heat: "#ea580c", // orange
  mass: "#6b7280", // grey
};

const MAX_PER_LAYER = 90;

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a) => Math.hypot(a[0], a[1], a[2]);
const unit = (a) => {
  const l = len(a);
  return l > 1e-30 ? [a[0] / l, a[1] / l, a[2] / l] : null;
};

/** Evenly thin a long list down to `max` entries so a fixed face does not drown the model in glyphs. */
function thin(list, max = MAX_PER_LAYER) {
  if (list.length <= max) return list;
  const step = list.length / max;
  return Array.from({ length: max }, (_, i) => list[Math.floor(i * step)]);
}

/** Outward unit normal and centroid of every (deduplicated) face, oriented away from the model centre. */
function faceFrames(nodes, faces, centre) {
  const out = [];
  for (const t of faces) {
    const a = nodes[t[0]], b = nodes[t[1]], c = nodes[t[2]];
    if (!a || !b || !c) continue;
    const n = unit(cross(sub(b, a), sub(c, a)));
    if (!n) continue;
    const g = [(a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3];
    out.push({ p: g, n: dot(n, sub(g, centre)) < 0 ? [-n[0], -n[1], -n[2]] : n });
  }
  return out;
}

/**
 * Build the glyph layers for a project.
 * @param project  API project (nodes, boundary_conditions, loads, reference_points, analysis_type)
 * @param visible  Set of node ids that may carry glyphs (surface nodes of the visible parts)
 */
export function buildGlyphs(project, visible) {
  if (!project?.nodes?.length) return [];
  const nodes = project.nodes;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const p of nodes) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[k]); hi[k] = Math.max(hi[k], p[k]); }
  const centre = lo.map((v, k) => (v + hi[k]) / 2);
  const span = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 1e-9);
  const onSurface = (ids) => thin(Array.from(new Set(ids || [])).filter((n) => visible.has(n) && nodes[n]));
  const facesOf = (faces) => thin(faceFrames(nodes, (faces || []).filter((t) => t.every((n) => visible.has(n))), centre));

  const thermalOnly = ["thermal", "thermal_transient"].includes(project.analysis_type);
  const layers = [];
  const add = (shape, color, items) => { if (items.length) layers.push({ shape, color, items }); };

  /* ---- supports: one cone per restrained translation, pointing at the node from outside the body */
  const cones = [], temps = [];
  (project.boundary_conditions || []).forEach((b) => {
    if (b.ref_id) return;
    if (b.bc_type === "temperature" || (!b.bc_type && thermalOnly)) {
      onSurface(b.node_ids).forEach((n) => temps.push({ p: nodes[n], d: [0, 0, 1] }));
      return;
    }
    const axes = (b.dofs || []).filter((d) => d >= 0 && d < 3);
    onSurface(b.node_ids).forEach((n) => {
      const p = nodes[n];
      axes.forEach((ax) => {
        const side = p[ax] - centre[ax] >= 0 ? 1 : -1; // sits on the outer side of the model
        const d = [0, 0, 0];
        d[ax] = -side;
        cones.push({ p, d });
      });
    });
  });
  add("cone", GLYPH_COLORS.support, thin(cones, MAX_PER_LAYER * 2));
  add("sphere", GLYPH_COLORS.temperature, temps);

  /* ---- loads */
  const arrows = [], heatArrows = [], heatBalls = [], masses = [];
  const refs = new Map((project.reference_points || []).map((r) => [r.id, r.coords]));
  (project.loads || []).forEach((l) => {
    const v = l.vector || [0, 0, 0];
    const dirV = unit(v);
    switch (l.load_type) {
      case "force":
        if (dirV) onSurface(l.node_ids).forEach((n) => arrows.push({ p: nodes[n], d: dirV }));
        break;
      case "ref_force": {
        const c = refs.get(l.ref_id);
        if (c && dirV) arrows.push({ p: c, d: dirV });
        break;
      }
      case "traction":
        if (dirV) facesOf(l.faces).forEach((f) => arrows.push({ p: f.p, d: dirV }));
        break;
      case "pressure": {
        const s = (l.magnitude ?? 0) >= 0 ? -1 : 1; // positive pressure pushes into the surface
        facesOf(l.faces).forEach((f) => arrows.push({ p: f.p, d: [f.n[0] * s, f.n[1] * s, f.n[2] * s] }));
        break;
      }
      case "heat_flux":
        onSurface(l.node_ids).forEach((n) => heatBalls.push({ p: nodes[n], d: [0, 0, 1] }));
        break;
      case "surface_flux":
      case "convection":
      case "radiation": {
        const s = l.load_type === "surface_flux" && (l.magnitude ?? 0) < 0 ? 1 : -1; // heat into the body by default
        facesOf(l.faces).forEach((f) => heatArrows.push({ p: f.p, d: [f.n[0] * s, f.n[1] * s, f.n[2] * s] }));
        break;
      }
      case "point_mass":
        onSurface(l.node_ids).forEach((n) => masses.push({ p: nodes[n], d: [0, 0, 1] }));
        break;
      default:
    }
  });
  const g = project.gravity;
  if (g?.enabled) {
    const d = unit([g.x || 0, g.y || 0, g.z || 0]);
    if (d) {
      // one gravity arrow hanging beside the model, like a PrePoMax gravity symbol
      arrows.push({ p: [centre[0], centre[1], hi[2] + span * 0.25], d });
    }
  }
  add("arrow", GLYPH_COLORS.force, arrows);
  add("arrow", GLYPH_COLORS.heat, heatArrows);
  add("sphere", GLYPH_COLORS.heat, heatBalls);
  add("sphere", GLYPH_COLORS.mass, masses);
  return layers;
}

/** Glyph size for a model: bigger when there are few glyphs, smaller when the busiest layer is crowded. */
export function glyphSize(diag, busiestLayer) {
  const f = busiestLayer <= 8 ? 0.11 : busiestLayer <= 20 ? 0.08 : busiestLayer <= 40 ? 0.055 : 0.042;
  return diag * f;
}
