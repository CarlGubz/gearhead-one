import type { FieldName, FieldsPayload, SlicePlane } from "../types";

export interface DecodedFields {
  nx: number;
  ny: number;
  lx: number;
  ly: number;
  /** names of the two in-plane axes (x, y for 2-D; the slice axes for 3-D) */
  axes: [string, string];
  /** in-plane velocity components drawn as vectors / streamlines */
  va: Float32Array;
  vb: Float32Array;
  vectors: [FieldName, FieldName];
  dim: 2 | 3;
  plane: SlicePlane | null;
  backend: string;
  time: number;
  frame: number;
  nFrames: number;
  energy: boolean;
  iteration: number | null;
  mask: Uint8Array; // row-major (ny rows of nx), 1 = solid
  data: Record<FieldName, Float32Array>;
  ranges: Record<FieldName, [number, number]>;
}

function b64ToBytes(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function decodeFields(p: FieldsPayload): DecodedFields {
  const data = {} as Record<FieldName, Float32Array>;
  for (const k of Object.keys(p.fields) as FieldName[]) {
    const bytes = b64ToBytes(p.fields[k]!);
    data[k] = new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
  }
  const vectors = (p.vectors ?? ["u", "v"]) as [FieldName, FieldName];
  return {
    nx: p.nx, ny: p.ny, lx: p.lx, ly: p.ly, time: p.time, frame: p.frame, nFrames: p.n_frames, energy: p.energy,
    iteration: p.iteration, mask: b64ToBytes(p.mask), data, ranges: p.ranges as Record<FieldName, [number, number]>,
    axes: (p.axes ?? ["x", "y"]) as [string, string], vectors, va: data[vectors[0]], vb: data[vectors[1]],
    dim: p.dim ?? 2, plane: p.plane ?? null, backend: p.backend ?? "native",
  };
}

export const FIELD_LABELS: Record<FieldName, { label: string; unit: string }> = {
  speed: { label: "Velocity magnitude", unit: "m/s" },
  u: { label: "Velocity x", unit: "m/s" },
  v: { label: "Velocity y", unit: "m/s" },
  w: { label: "Velocity z", unit: "m/s" },
  p: { label: "Pressure (gauge)", unit: "Pa" },
  T: { label: "Temperature", unit: "K" },
  vorticity: { label: "Vorticity (normal to view)", unit: "1/s" },
};

export function formatNumber(v: number, digits = 4): string {
  if (!Number.isFinite(v)) return "–";
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(digits - 1);
  return Number(v.toPrecision(digits)).toString();
}

/** Bilinear velocity sample at physical (x, y); returns null inside solids / outside domain. Allocation-free hot path. */
const _out: [number, number] = [0, 0];
export function sampleVelocity(f: DecodedFields, x: number, y: number): [number, number] | null {
  const { nx, ny, lx, ly } = f;
  if (x < 0 || y < 0 || x > lx || y > ly) return null;
  const dx = lx / nx, dy = ly / ny;
  const ci = Math.min(nx - 1, (x / dx) | 0), cj = Math.min(ny - 1, (y / dy) | 0);
  if (f.mask[cj * nx + ci]) return null;
  let fx = x / dx - 0.5, fy = y / dy - 0.5;
  fx = fx < 0 ? 0 : fx > nx - 1 ? nx - 1 : fx;
  fy = fy < 0 ? 0 : fy > ny - 1 ? ny - 1 : fy;
  const i0 = fx | 0, j0 = fy | 0;
  const i1 = i0 + 1 < nx ? i0 + 1 : i0, j1 = j0 + 1 < ny ? j0 + 1 : j0;
  const a = fx - i0, b = fy - j0;
  const k00 = j0 * nx + i0, k10 = j0 * nx + i1, k01 = j1 * nx + i0, k11 = j1 * nx + i1;
  const w00 = f.mask[k00] ? 0 : (1 - a) * (1 - b), w10 = f.mask[k10] ? 0 : a * (1 - b);
  const w01 = f.mask[k01] ? 0 : (1 - a) * b, w11 = f.mask[k11] ? 0 : a * b;
  const ws = w00 + w10 + w01 + w11;
  if (ws <= 0) return null;
  const U = f.va, V = f.vb;
  _out[0] = (w00 * U[k00] + w10 * U[k10] + w01 * U[k01] + w11 * U[k11]) / ws;
  _out[1] = (w00 * V[k00] + w10 * V[k10] + w01 * V[k01] + w11 * V[k11]) / ws;
  return _out;
}

/**
 * Evenly-spaced streamlines (RK2). Lines may not enter a coarse bin that already belongs to
 * another line, which keeps the picture clean and the cost proportional to the number of bins
 * (fast enough to recompute for every animation frame).
 */
export function streamlines(f: DecodedFields, maxSteps = 400): number[][] {
  const dx = f.lx / f.nx, dy = f.ly / f.ny;
  const sp = Math.max(2, Math.round(Math.max(f.nx, f.ny) / 50));
  const bx = Math.ceil(f.nx / sp), by = Math.ceil(f.ny / sp);
  const occ = new Int32Array(bx * by).fill(-1);
  const binOf = (x: number, y: number) =>
    Math.min(by - 1, Math.max(0, Math.floor(y / dy / sp))) * bx + Math.min(bx - 1, Math.max(0, Math.floor(x / dx / sp)));
  const h = 0.6 * Math.min(dx, dy);
  const lines: number[][] = [];
  const trace = (x0: number, y0: number, dir: 1 | -1, id: number): number[] => {
    const pts: number[] = [];
    let x = x0, y = y0;
    for (let s = 0; s < maxSteps; s++) {
      const v1 = sampleVelocity(f, x, y);
      if (!v1) break;
      const v1x = v1[0], v1y = v1[1];
      const m1 = Math.hypot(v1x, v1y);
      if (m1 < 1e-9) break;
      const v2 = sampleVelocity(f, x + (dir * h * 0.5 * v1x) / m1, y + (dir * h * 0.5 * v1y) / m1);
      if (!v2) break;
      const m2 = Math.hypot(v2[0], v2[1]);
      if (m2 < 1e-9) break;
      x += (dir * h * v2[0]) / m2;
      y += (dir * h * v2[1]) / m2;
      if (x < 0 || y < 0 || x > f.lx || y > f.ly) break;
      const b = binOf(x, y);
      if (occ[b] !== -1 && occ[b] !== id) break;
      occ[b] = id;
      pts.push(x, y);
    }
    return pts;
  };
  let id = 0;
  for (let j = 0; j < by; j++) {
    for (let i = 0; i < bx; i++) {
      if (occ[j * bx + i] !== -1) continue;
      const x = Math.min(f.lx, (i + 0.5) * sp * dx), y = Math.min(f.ly, (j + 0.5) * sp * dy);
      if (!sampleVelocity(f, x, y)) continue;
      occ[binOf(x, y)] = id;
      const fwd = trace(x, y, 1, id);
      const bwd = trace(x, y, -1, id);
      const line: number[] = [];
      for (let k = bwd.length - 2; k >= 0; k -= 2) line.push(bwd[k], bwd[k + 1]); // reverse by (x, y) pair
      line.push(x, y, ...fwd);
      id++;
      if (line.length >= 8) lines.push(line);
    }
  }
  return lines;
}
