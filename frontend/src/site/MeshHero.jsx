import React, { useEffect, useRef } from "react";
import { ContourBar } from "../ui";
import "./site.css";

/*
  The signature visual: a cantilever beam meshed with triangles, bending under
  a pulsing tip load, coloured by (approximate) bending stress with the same
  contour ramp the product uses for results. Canvas 2D, no dependencies.

  - Honors prefers-reduced-motion (renders one still frame).
  - Pauses when scrolled off-screen or the tab is hidden.
  - Re-reads theme colours from CSS variables when <html data-theme> changes.
*/

const NX = 26; // cells along the beam
const NY = 6; // cells through the depth
const BEAM_H = 0.17; // depth / length
const MAX_DEFLECTION = 0.24; // tip deflection / length at peak load
const ASPECT = 1.7;

const hex = (h) => {
  let s = h.trim().replace("#", "");
  if (s.length === 3) s = s.split("").map((c) => c + c).join("");
  const n = parseInt(s, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

function readColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim() || "#000";
  return {
    stops: [0, 1, 2, 3, 4, 5].map((i) => hex(v(`--gh-c${i}`))),
    ink: v("--gh-ink"),
    ink2: v("--gh-ink-2"),
    accent: v("--gh-accent"),
    paper: v("--gh-paper"),
  };
}

function rampColor(stops, t) {
  const c = Math.min(1, Math.max(0, t));
  const p = c * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(p));
  const f = p - i;
  const a = stops[i];
  const b = stops[i + 1];
  return `rgb(${(a[0] + (b[0] - a[0]) * f) | 0},${(a[1] + (b[1] - a[1]) * f) | 0},${(a[2] + (b[2] - a[2]) * f) | 0})`;
}

// Rest-state mesh in beam units: X in [0,1], Y in [-h/2, h/2], plus a
// normalised stress pattern (largest at the fixed end and outer fibres).
function buildMesh() {
  const nodes = [];
  for (let j = 0; j <= NY; j++) {
    for (let i = 0; i <= NX; i++) {
      const X = i / NX;
      const Y = (j / NY - 0.5) * BEAM_H;
      const outer = Math.abs(Y) / (BEAM_H / 2);
      // bending moment falls linearly to the tip; fibre stress grows with |y|;
      // a bump at the wall corners mimics the stress concentration.
      const corner = Math.exp(-X * 22) * outer * outer * 0.35;
      nodes.push({ X, Y, s0: (1 - X) * (0.1 + 0.9 * outer) + corner });
    }
  }
  return nodes;
}

function draw(ctx, W, H, dpr, colors, nodes, tSec, still) {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const L = W * 0.8;
  const ox = W * 0.1;
  const oy = W * 0.205;
  const amp = still ? 0.85 : 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(tSec * 1.15 - Math.PI / 2));
  const d = amp * MAX_DEFLECTION;
  const cols = NX + 1;

  // displaced positions + nodal stress
  const px = new Float32Array(nodes.length);
  const py = new Float32Array(nodes.length);
  const sv = new Float32Array(nodes.length);
  for (let k = 0; k < nodes.length; k++) {
    const { X, Y, s0 } = nodes[k];
    const w = ((3 * X * X - X * X * X) / 2) * d; // cantilever elastic curve
    const th = ((6 * X - 3 * X * X) / 2) * d; // its slope
    px[k] = ox + (X - Y * th) * L; // plane sections stay plane
    py[k] = oy + (Y + w) * L;
    sv[k] = s0 * (0.1 + 0.9 * amp) * 1.05;
  }

  const lw = Math.max(1, W / 640);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.font = `500 ${Math.max(10, W / 58)}px "JetBrains Mono Variable", ui-monospace, monospace`;

  // fixed wall with hatching
  const top = oy - (BEAM_H / 2) * L - 10;
  const bot = oy + (BEAM_H / 2) * L + 10;
  ctx.strokeStyle = colors.ink;
  ctx.lineWidth = lw * 2.2;
  ctx.beginPath();
  ctx.moveTo(ox, top);
  ctx.lineTo(ox, bot);
  ctx.stroke();
  ctx.lineWidth = lw;
  ctx.beginPath();
  for (let y = top + 6; y < bot + 12; y += 11 * (W / 640)) {
    ctx.moveTo(ox, y);
    ctx.lineTo(ox - 14 * (W / 640), y + 12 * (W / 640));
  }
  ctx.stroke();

  // undeformed outline (dashed)
  ctx.save();
  ctx.setLineDash([5 * lw, 5 * lw]);
  ctx.strokeStyle = colors.ink2;
  ctx.globalAlpha = 0.55;
  ctx.strokeRect(ox, oy - (BEAM_H / 2) * L, L, BEAM_H * L);
  ctx.restore();

  // shaded triangles
  for (let j = 0; j < NY; j++) {
    for (let i = 0; i < NX; i++) {
      const a = j * cols + i;
      const b = a + 1;
      const c = a + cols + 1;
      const e = a + cols;
      const tri = (n1, n2, n3) => {
        ctx.beginPath();
        ctx.moveTo(px[n1], py[n1]);
        ctx.lineTo(px[n2], py[n2]);
        ctx.lineTo(px[n3], py[n3]);
        ctx.closePath();
        const col = rampColor(colors.stops, (sv[n1] + sv[n2] + sv[n3]) / 3);
        ctx.fillStyle = col;
        ctx.strokeStyle = col;
        ctx.lineWidth = 0.8;
        ctx.fill();
        ctx.stroke();
      };
      tri(a, b, c);
      tri(a, c, e);
    }
  }

  // wireframe on top
  ctx.strokeStyle = colors.ink;
  ctx.globalAlpha = 0.3;
  ctx.lineWidth = lw * 0.7;
  ctx.beginPath();
  for (let j = 0; j <= NY; j++) {
    for (let i = 0; i <= NX; i++) {
      const k = j * cols + i;
      if (i < NX) {
        ctx.moveTo(px[k], py[k]);
        ctx.lineTo(px[k + 1], py[k + 1]);
      }
      if (j < NY) {
        ctx.moveTo(px[k], py[k]);
        ctx.lineTo(px[k + cols], py[k + cols]);
      }
      if (i < NX && j < NY) {
        ctx.moveTo(px[k], py[k]);
        ctx.lineTo(px[k + cols + 1], py[k + cols + 1]);
      }
    }
  }
  ctx.stroke();
  ctx.globalAlpha = 1;

  // beam outline
  ctx.strokeStyle = colors.ink;
  ctx.lineWidth = lw * 1.6;
  ctx.beginPath();
  for (let i = 0; i <= NX; i++) ctx[i ? "lineTo" : "moveTo"](px[i], py[i]);
  for (let j = 1; j <= NY; j++) ctx.lineTo(px[j * cols + NX], py[j * cols + NX]);
  for (let i = NX - 1; i >= 0; i--) ctx.lineTo(px[NY * cols + i], py[NY * cols + i]);
  ctx.closePath();
  ctx.stroke();

  // tip load arrow (scales with load)
  const tipX = px[NX];
  const tipY = py[NX];
  const len = (28 + 34 * amp) * (W / 640);
  ctx.strokeStyle = colors.accent;
  ctx.fillStyle = colors.accent;
  ctx.lineWidth = lw * 2.4;
  ctx.beginPath();
  ctx.moveTo(tipX, tipY - len - 6);
  ctx.lineTo(tipX, tipY - 10);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(tipX, tipY - 3);
  ctx.lineTo(tipX - 6 * (W / 640), tipY - 16 * (W / 640));
  ctx.lineTo(tipX + 6 * (W / 640), tipY - 16 * (W / 640));
  ctx.closePath();
  ctx.fill();
  ctx.fillText("F", tipX + 10 * (W / 640), tipY - len / 2 - 6);

  // dimension line under the beam
  const dy = oy + (BEAM_H / 2) * L + MAX_DEFLECTION * L + W * 0.04;
  ctx.strokeStyle = colors.ink2;
  ctx.fillStyle = colors.ink2;
  ctx.lineWidth = lw;
  ctx.beginPath();
  ctx.moveTo(ox, dy);
  ctx.lineTo(ox + L, dy);
  ctx.moveTo(ox, dy - 7);
  ctx.lineTo(ox, dy + 7);
  ctx.moveTo(ox + L, dy - 7);
  ctx.lineTo(ox + L, dy + 7);
  ctx.stroke();
  const label = "L = 240 mm";
  const tw = ctx.measureText(label).width;
  ctx.fillStyle = colors.paper;
  ctx.fillRect(ox + L / 2 - tw / 2 - 8, dy - 9, tw + 16, 18);
  ctx.fillStyle = colors.ink2;
  ctx.textBaseline = "middle";
  ctx.fillText(label, ox + L / 2 - tw / 2, dy + 1);
  ctx.textBaseline = "alphabetic";

  return amp;
}

export default function MeshHero({ className = "", caption = true, legend = true }) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const maxRef = useRef(null);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const nodes = buildMesh();
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let colors = readColors();
    let W = 0;
    let H = 0;
    let dpr = 1;
    let raf = 0;
    let visible = true;
    let t0 = performance.now();
    let lastReadout = -1;

    const resize = () => {
      const r = wrap.getBoundingClientRect();
      if (!r.width) return;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = r.width;
      H = r.width / ASPECT;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.height = `${H}px`;
      frame(performance.now());
    };

    const frame = (now) => {
      const amp = draw(ctx, W, H, dpr, colors, nodes, (now - t0) / 1000, reduced);
      // keep the live readout in sync without re-rendering React every frame
      const pct = Math.round(amp * 100);
      if (maxRef.current && pct !== lastReadout) {
        lastReadout = pct;
        maxRef.current.textContent = `LOAD ${String(pct).padStart(3, " ")}%`;
      }
    };

    const loop = (now) => {
      frame(now);
      raf = requestAnimationFrame(loop);
    };
    const start = () => {
      if (reduced || raf || !visible || document.hidden) return;
      raf = requestAnimationFrame(loop);
    };
    const stop = () => {
      cancelAnimationFrame(raf);
      raf = 0;
    };

    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      visible ? start() : stop();
    });
    io.observe(wrap);
    const onVis = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVis);
    const mo = new MutationObserver(() => {
      colors = readColors();
      frame(performance.now());
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    resize();
    start();
    return () => {
      stop();
      ro.disconnect();
      io.disconnect();
      mo.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  return (
    <figure className={`gh-hero-fig ${className}`}>
      <div ref={wrapRef} className="gh-hero-fig__frame">
        <canvas
          ref={canvasRef}
          role="img"
          aria-label="Animated finite element mesh of a cantilever beam bending under a tip load, coloured by stress from blue (low) to red (high)."
        />
        <span className="gh-hero-fig__readout gh-mono" ref={maxRef} aria-hidden="true" />
      </div>
      {(caption || legend) && (
        <figcaption className="gh-hero-fig__cap">
          {caption && (
            <span className="gh-mono">
              Fig. 01 — Cantilever, von Mises stress <span className="gh-hero-fig__live">● live</span>
            </span>
          )}
          {legend && <ContourBar min="0 MPa" max="σ max" />}
        </figcaption>
      )}
    </figure>
  );
}
