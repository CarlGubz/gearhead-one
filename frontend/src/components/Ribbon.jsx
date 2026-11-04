import React, { useEffect, useRef, useState } from "react";
import Icon from "./icons.jsx";

export const WORKSPACES = [
  { id: "fea", label: "FEA", name: "Structures", icon: "fea",
    hint: "Finite element analysis: geometry & meshing, parts, materials, supports, loads, 8 analysis types, results." },
  { id: "cfd", label: "CFD", name: "Fluids", icon: "cfd",
    hint: "Computational fluid dynamics: 2-D / 3-D cases, native, OpenFOAM, FEniCSx and PyFR solvers, live runs, flow results." },
  { id: "cad", label: "CAD", name: "Design", icon: "cad",
    hint: "Parametric CAD / CAM: sketches, 3-D features, assemblies, drawing sheets, CAM toolpaths and CAD simulation." },
];

/** Multiple Functions button: picks the FEA / CFD / CAD workspace, which decides the ribbon's commands and the body. */
function WorkspaceSwitcher({ workspace, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const cur = WORKSPACES.find((w) => w.id === workspace) || WORKSPACES[0];
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="ribbon-workspace" ref={ref} data-tour="workspace">
      <button className={`ribbon-ws-btn ${open ? "open" : ""}`} onClick={() => setOpen((o) => !o)}
        title="Multiple Functions: switch between the FEA, CFD and CAD workspaces of this project" aria-haspopup="menu" aria-expanded={open}>
        <Icon name={cur.icon} size={22} />
        <span className="ribbon-ws-text">
          <span className="ribbon-ws-kicker">Functions</span>
          <span className="ribbon-ws-name">{cur.label} · {cur.name}</span>
        </span>
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <ul className="ribbon-menu" role="menu">
          {WORKSPACES.map((w) => (
            <li key={w.id} role="none">
              <button role="menuitemradio" aria-checked={w.id === workspace} className={w.id === workspace ? "current" : ""}
                onClick={() => { onChange(w.id); setOpen(false); }}>
                <Icon name={w.icon} size={22} />
                <span><strong>{w.label} · {w.name}</strong><small>{w.hint}</small></span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Fusion-style command ribbon. Each command is an icon with a label and a ▾ arrow below it; clicking either opens the
 * command's dialog (its sub-options). Commands with `action` run immediately instead (Solve, Undo, Redo); `plain` keeps an
 * action's icon in the normal text colour instead of the accent; `current` marks the command whose view is showing (the
 * CFD Setup / Run / Results pages, the CAD Sketch … Simulate workspaces).
 * groups: [{ id, label, icon, title, disabled, badge, tour, action, plain, current } | { sep: true }]
 */
export default function Ribbon({ workspace, onWorkspace, groups, openId, onOpen }) {
  return (
    <div className="ribbon">
      <WorkspaceSwitcher workspace={workspace} onChange={onWorkspace} />
      <div className="ribbon-groups" role="toolbar" aria-label="Commands">
        {groups.map((g, i) => (g.sep ? <span key={`sep${i}`} className="ribbon-sep" /> : (
          <div key={g.id} className={`ribbon-group ${openId === g.id ? "open" : ""} ${g.current ? "current" : ""} ${g.action ? "action" : ""} ${g.plain ? "plain" : ""}`}
            data-tour={g.tour}>
            <button className="ribbon-icon" disabled={g.disabled} title={g.title}
              onClick={() => (g.action ? g.action() : onOpen(g.id))}>
              <Icon name={g.icon} size={26} />
              {g.badge !== undefined && g.badge !== null && <span className="ribbon-badge">{g.badge}</span>}
            </button>
            <button className="ribbon-label" disabled={g.disabled} title={g.action ? g.title : `${g.label}: show options`}
              aria-haspopup={g.action ? undefined : "dialog"} aria-expanded={g.action ? undefined : openId === g.id}
              onClick={() => (g.action ? g.action() : onOpen(g.id))}>
              {g.label}
              {!g.action && <Icon name="chevronDown" size={11} />}
            </button>
          </div>
        )))}
      </div>
    </div>
  );
}
