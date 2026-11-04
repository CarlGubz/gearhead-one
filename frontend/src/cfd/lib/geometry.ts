import type { CaseConfig, Obstacle } from "../types";

/** Rasterise obstacles on the mesh exactly like the backend (cell-centre inside shape). Index = j*nx + i. */
export function rasterize(cfg: Pick<CaseConfig, "domain" | "mesh" | "obstacles">): Uint8Array {
  const { nx, ny } = cfg.mesh;
  const dx = cfg.domain.width / nx;
  const dy = cfg.domain.height / ny;
  const mask = new Uint8Array(nx * ny);
  for (const ob of cfg.obstacles) {
    if (ob.shape === "mesh") {
      rasterizeMesh(ob, nx, ny, dx, dy, mask);
      continue;
    }
    for (let j = 0; j < ny; j++) {
      const y = (j + 0.5) * dy;
      for (let i = 0; i < nx; i++) {
        const x = (i + 0.5) * dx;
        if (insideObstacle(ob, x, y)) mask[j * nx + i] = 1;
      }
    }
  }
  return mask;
}

export function insideObstacle(ob: Obstacle, x: number, y: number): boolean {
  // spheres and z-limited bodies are drawn as their projection onto the x-y plane
  if (ob.shape === "rectangle") return Math.abs(x - ob.x) <= ob.width / 2 && Math.abs(y - ob.y) <= ob.height / 2;
  if (ob.shape === "mesh") {
    const sec = meshSection(ob);
    if (!sec) return false;
    const yq = y - ob.y + sec.jy;
    let n = 0;
    for (const [x1, y1, x2, y2] of sec.segments) {
      if ((y1 > yq) !== (y2 > yq) && x1 + ((yq - y1) / (y2 - y1)) * (x2 - x1) < x - ob.x) n++;
    }
    return n % 2 === 1;
  }
  return (x - ob.x) ** 2 + (y - ob.y) ** 2 <= ob.radius ** 2;
}

/* ------------------------------------------------------------------ imported solids ("mesh" obstacles) */
/**
 * An imported solid (FEA model / CAD part sent to CFD) is a closed triangle surface with vertices relative to the
 * obstacle centre. The canvas works on its section with the plane through the centre (z = 0 of the vertices) - the
 * section the 2-D solver uses; 3-D cases show the same section. Like the backend (solver/meshsolid.py) the cut and
 * the scan lines are offset by a tiny fraction of the body size, so the cells coloured here are the solver's cells.
 */
interface Section {
  segments: [number, number, number, number][];
  jy: number;
  lo: [number, number];
  hi: [number, number];
}

const sections = new WeakMap<object, Section | null>();
const JITTER_Y = 1.2345678e-7;
const JITTER_Z = 2.3456789e-7;

export function meshSection(ob: Obstacle): Section | null {
  const V = ob.vertices, T = ob.triangles;
  if (!V || !T || V.length < 3) return null;
  const cached = sections.get(V);
  if (cached !== undefined) return cached;
  let span = 0;
  for (let a = 0; a < 3; a++) {
    let mn = Infinity, mx = -Infinity;
    for (const p of V) { mn = Math.min(mn, p[a]); mx = Math.max(mx, p[a]); }
    span = Math.max(span, mx - mn);
  }
  span = span || 1;
  const zq = JITTER_Z * span;
  const segments: Section["segments"] = [];
  const lo: [number, number] = [Infinity, Infinity], hi: [number, number] = [-Infinity, -Infinity];
  for (const [ia, ib, ic] of T) {
    const pts: [number, number][] = [];
    const tri = [V[ia], V[ib], V[ic]];
    for (let e = 0; e < 3; e++) {
      const p = tri[e], q = tri[(e + 1) % 3];
      if ((p[2] > zq) !== (q[2] > zq)) {
        const t = (zq - p[2]) / (q[2] - p[2]);
        pts.push([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]);
      }
    }
    if (pts.length === 2) {
      segments.push([pts[0][0], pts[0][1], pts[1][0], pts[1][1]]);
      for (const [x, y] of pts) { lo[0] = Math.min(lo[0], x); lo[1] = Math.min(lo[1], y); hi[0] = Math.max(hi[0], x); hi[1] = Math.max(hi[1], y); }
    }
  }
  const sec = segments.length ? { segments, jy: JITTER_Y * span, lo, hi } : null;
  sections.set(V, sec);
  return sec;
}

function rasterizeMesh(ob: Obstacle, nx: number, ny: number, dx: number, dy: number, mask: Uint8Array) {
  const sec = meshSection(ob);
  if (!sec) return;
  for (let j = 0; j < ny; j++) {
    const yq = (j + 0.5) * dy - ob.y + sec.jy;
    if (yq < sec.lo[1] - dy || yq > sec.hi[1] + dy) continue;
    const xs: number[] = [];
    for (const [x1, y1, x2, y2] of sec.segments) {
      if ((y1 > yq) !== (y2 > yq)) xs.push(ob.x + x1 + ((yq - y1) / (y2 - y1)) * (x2 - x1));
    }
    if (xs.length < 2) continue;
    xs.sort((a, b) => a - b);
    for (let i = 0; i < nx; i++) {
      const x = (i + 0.5) * dx;
      let n = 0;
      while (n < xs.length && xs[n] < x) n++;
      if (n % 2 === 1) mask[j * nx + i] = 1;
    }
  }
}

/** Half extents (x, y) of an obstacle's footprint on the canvas. */
export function footprint(ob: Obstacle): [number, number] {
  if (ob.shape === "rectangle" || ob.shape === "mesh") return [ob.width / 2, ob.height / 2];
  return [ob.radius, ob.radius];
}

export function nextId(prefix: string, existing: { id: string }[]): string {
  let n = existing.length + 1;
  const ids = new Set(existing.map((e) => e.id));
  while (ids.has(`${prefix}${n}`)) n++;
  return `${prefix}${n}`;
}

/** Round to 4 significant digits to keep dragged coordinates tidy. */
export function tidy(v: number): number {
  return Number.isFinite(v) ? Number(v.toPrecision(4)) : 0;
}
