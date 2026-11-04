import React from "react";

/**
 * Line icons for the ribbon, the browser panels and the command dialogs. 24 × 24 grid, stroked with currentColor so
 * they follow the theme; an optional `accent` path is filled with the workspace accent colour.
 */
const PATHS = {
  project: { d: "M3 6.5h6l2 2h10v10.5H3z M3 6.5V5h6l2 2" },
  geometry: { d: "M12 3l8 4.5v9L12 21l-8-4.5v-9z M12 12l8-4.5 M12 12v9 M12 12L4 7.5", accent: "M12 3l8 4.5L12 12 4 7.5z" },
  structural: { d: "M3 19h18 M4 19L8 7l4 12 4-12 4 12 M8 7h8", accent: "M7 6h2v2H7z M15 6h2v2h-2z" },
  parts: { d: "M3 9l5-3 5 3v6l-5 3-5-3z M11 6l5-3 5 3v6l-5 3", accent: "M3 9l5-3 5 3-5 3z" },
  materials: { d: "M12 3a9 9 0 100 18 9 9 0 000-18z M5 9c3 2 11 2 14 0 M4.5 14c3.5 2.2 11.5 2.2 15 0", accent: "M8.5 6.2a4 4 0 013.5-1.7v3.2a2 2 0 00-1.8 1z" },
  selection: { d: "M5 4l13 6-5.5 1.8L10.7 18z M13 12.5l5 5", accent: "M5 4l13 6-5.5 1.8L10.7 18z" },
  analysis: { d: "M4 20V4 M4 20h16 M7 16l4-5 3 3 5-7" },
  loads: { d: "M12 3v11 M8 10l4 4 4-4 M4 20h16 M6 17l-2 3 M10 17l-2 3 M14 17l-2 3 M18 17l-2 3" },
  constraints: { d: "M9 7a3 3 0 106 0 3 3 0 00-6 0z M7 15a3 3 0 106 0 3 3 0 00-6 0z M13 15a3 3 0 106 0 3 3 0 00-6 0z M12 10v2" },
  solve: { d: "M12 3a9 9 0 100 18 9 9 0 000-18z", accent: "M10 8l6 4-6 4z" },
  results: { d: "M4 20h16 M6 16V9 M10 16V5 M14 16v-4 M18 16V8", accent: "M5 9h2v7H5z" },
  cad: { d: "M4 18l5-12h6l5 12z M8 14h8", accent: "M9 6h6l1.7 4H7.3z" },
  cam: { d: "M10 3h4v7l-2 3-2-3z M5 21h14v-4H5z M12 13v4", accent: "M10 3h4v3h-4z" },
  cae: { d: "M3 17c3-1 4-9 9-9s6 8 9 9 M3 20h18", accent: "M12 8a1.6 1.6 0 110 3.2A1.6 1.6 0 0112 8z" },
  stock: { d: "M4 8l8-4 8 4v8l-8 4-8-4z M4 8l8 4 8-4 M12 12v8" },
  toolpath: { d: "M4 18c2-6 4-6 6 0s4 6 6 0 3-6 4-6 M4 6h16" },
  simulate: { d: "M5 4v16l14-8z" },
  post: { d: "M6 3h9l4 4v14H6z M15 3v4h4 M9 12h7 M9 16h7" },
  undo: { d: "M9 7L4 12l5 5 M4 12h11a5 5 0 010 10h-2" },
  redo: { d: "M15 7l5 5-5 5 M20 12H9a5 5 0 000 10h2" },
  plus: { d: "M12 5v14 M5 12h14" },
  duplicate: { d: "M8 8h12v12H8z M4 16V4h12" },
  up: { d: "M12 19V5 M6 11l6-6 6 6" },
  down: { d: "M12 5v14 M6 13l6 6 6-6" },
  trash: { d: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 11v6 M14 11v6" },
  eye: { d: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M12 9a3 3 0 100 6 3 3 0 000-6z" },
  eyeOff: { d: "M3 3l18 18 M10.6 5.1A10 10 0 0112 5c6 0 10 7 10 7a17 17 0 01-3.2 3.9 M6.6 6.6C3.8 8.4 2 12 2 12s4 7 10 7a9.6 9.6 0 004.4-1.1" },
  chevronDown: { d: "M6 9l6 6 6-6" },
  chevronRight: { d: "M9 6l6 6-6 6" },
  close: { d: "M6 6l12 12 M18 6L6 18" },
  mesh: { d: "M4 4h16v16H4z M4 12h16 M12 4v16 M4 4l16 16 M12 4l8 8 M4 12l8 8" },
  file: { d: "M6 3h9l4 4v14H6z M15 3v4h4" },
  history: { d: "M4 12a8 8 0 108-8 8 8 0 00-6.3 3 M4 4v4h4 M12 8v4l3 2" },
  layers: { d: "M12 3l9 5-9 5-9-5z M3 13l9 5 9-5 M3 17.5l9 5 9-5" },
  export: { d: "M12 3v12 M7 8l5-5 5 5 M4 15v5h16v-5" },
  search: { d: "M10.5 4a6.5 6.5 0 100 13 6.5 6.5 0 000-13z M15.5 15.5L20 20" },
  sets: { d: "M5 6h2v2H5z M11 6h2v2h-2z M17 6h2v2h-2z M5 12h2v2H5z M11 12h2v2h-2z M5 18h2v2H5z", accent: "M5 6h2v2H5z M11 12h2v2h-2z" },
  refpoint: { d: "M12 4v16 M4 12h16", accent: "M12 9.5a2.5 2.5 0 110 5 2.5 2.5 0 010-5z" },
  step: { d: "M4 18h5v-4h5v-4h6 M17 7l3 3-3 3" },
  field: { d: "M4 18c2-8 5-12 8-12s6 4 8 12", accent: "M4 18c2-8 5-12 8-12v12z" },
  // ---- workspaces (Functions switcher) and the CFD / CAD / Transfer commands
  fea: { d: "M4 18l8-13 8 13z M8 18l4-6.5 4 6.5 M12 5v6.5", accent: "M12 11.5l4 6.5H8z" },
  cfd: { d: "M3 8c3-2 5 2 8 0s5-2 8 0 M3 13c3-2 5 2 8 0s5-2 8 0 M3 18c3-2 5 2 8 0s5-2 8 0", accent: "M15 11.2a2 2 0 110 4 2 2 0 010-4z" },
  cases: { d: "M4 5h7v6H4z M13 5h7v6h-7z M4 13h7v6H4z M13 13h7v6h-7z", accent: "M4 5h7v6H4z" },
  setup: { d: "M4 7h10 M18 7h2 M4 17h4 M12 17h8 M14 5v4 M10 15v4", accent: "M14 5h2v4h-2z" },
  run: { d: "M4 19V5 M4 19h16 M7 15c2-6 4-8 6-8s3 4 7 4", accent: "M4 19h16v1H4z" },
  sketch2d: { d: "M4 20L16 8l3 3L7 23 M14 6l3-3 4 4-3 3 M4 4h6 M4 4v6", accent: "M14 6l3-3 4 4-3 3z" },
  model3d: { d: "M4 8l8-4 8 4v8l-8 4-8-4z M4 8l8 4 8-4 M12 12v8", accent: "M12 12l8-4v8l-8 4z" },
  assembly: { d: "M3 10h8v8H3z M13 6h8v8h-8z M11 14h2 M7 10V7h6", accent: "M13 6h8v8h-8z" },
  drawing: { d: "M3 4h18v16H3z M14 20v-5h7 M6 8h6 M6 11h4 M16 8a2 2 0 104 0 2 2 0 00-4 0z" },
  transfer: { d: "M4 8h13 M13 4l4 4-4 4 M20 16H7 M11 12l-4 4 4 4", accent: "M13 4l4 4-4 4z" },
  demo: { d: "M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z" },
};

export default function Icon({ name, size = 18, className = "", title }) {
  const p = PATHS[name];
  if (!p) return null;
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title && <title>{title}</title>}
      {p.accent && <path d={p.accent} fill="var(--ws-accent)" stroke="none" opacity="0.85" />}
      <path d={p.d} />
    </svg>
  );
}
