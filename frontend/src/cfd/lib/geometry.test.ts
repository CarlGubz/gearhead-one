import { describe, expect, it } from "vitest";
import { rasterize, tidy } from "./geometry";
import { colormap } from "./colormap";
import { formatNumber } from "./fields";

const base = {
  domain: { width: 1, height: 1, depth: 1 },
  mesh: { nx: 10, ny: 10, nz: 1 },
  obstacles: [
    { id: "a", name: "a", shape: "rectangle" as const, x: 0.5, y: 0.5, z: null, width: 0.2, height: 0.2, depth: null, radius: 0.1, thermal: "adiabatic" as const, temperature: 300 },
  ],
};

describe("rasterize", () => {
  it("marks cells whose centre lies inside a rectangle", () => {
    const m = rasterize(base);
    // centres at 0.45 and 0.55 are inside |x-0.5|<=0.1
    expect(Array.from(m).reduce((a, b) => a + b, 0)).toBe(4);
    expect(m[4 * 10 + 4]).toBe(1);
    expect(m[0]).toBe(0);
  });
  it("handles circles", () => {
    const m = rasterize({ ...base, obstacles: [{ ...base.obstacles[0], shape: "circle", radius: 0.2 }] });
    const n = Array.from(m).reduce((a, b) => a + b, 0);
    expect(n).toBeGreaterThan(8);
    expect(n).toBeLessThan(16);
  });
});

describe("helpers", () => {
  it("tidy rounds to 4 significant digits", () => expect(tidy(0.123456789)).toBe(0.1235));
  it("colormap clamps and interpolates", () => {
    expect(colormap("gray", -5)).toEqual(colormap("gray", 0));
    expect(colormap("gray", 9)).toEqual(colormap("gray", 1));
    const mid = colormap("gray", 0.5);
    expect(mid[0]).toBeCloseTo(127.5);
  });
  it("formatNumber", () => {
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(123456)).toMatch(/e\+5/);
    expect(formatNumber(1.23456)).toBe("1.235");
  });
});

import { streamlines, type DecodedFields } from "./fields";

describe("streamlines", () => {
  it("follow a uniform +x flow horizontally and stay inside the domain", () => {
    const nx = 40, ny = 10;
    const u = new Float32Array(nx * ny).fill(1), v = new Float32Array(nx * ny);
    const f: DecodedFields = {
      nx, ny, lx: 4, ly: 1, time: 0, frame: 0, nFrames: 1, energy: false, iteration: 1,
      axes: ["x", "y"], vectors: ["u", "v"], va: u, vb: v, dim: 2, plane: null, backend: "native",
      mask: new Uint8Array(nx * ny),
      data: { u, v, w: new Float32Array(nx * ny), speed: new Float32Array(nx * ny).fill(1), p: new Float32Array(nx * ny), T: new Float32Array(nx * ny), vorticity: new Float32Array(nx * ny) },
      ranges: { u: [1, 1], v: [0, 0], w: [0, 0], speed: [1, 1], p: [0, 0], T: [0, 0], vorticity: [0, 0] },
    };
    const lines = streamlines(f);
    expect(lines.length).toBeGreaterThan(3);
    for (const L of lines) {
      for (let k = 0; k < L.length; k += 2) {
        expect(L[k]).toBeGreaterThanOrEqual(0); expect(L[k]).toBeLessThanOrEqual(4);
        expect(L[k + 1]).toBeGreaterThanOrEqual(0); expect(L[k + 1]).toBeLessThanOrEqual(1);
        expect(L[k + 1]).toBeCloseTo(L[1], 6); // constant y => horizontal
      }
      for (let k = 2; k < L.length; k += 2) expect(L[k]).toBeGreaterThan(L[k - 2]); // monotone in x (no x/y swap)
    }
  });
});
