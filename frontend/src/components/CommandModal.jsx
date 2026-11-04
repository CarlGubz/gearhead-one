import React, { useEffect, useRef, useState } from "react";
import Icon from "./icons.jsx";

/**
 * Top-left corner of the visible workspace body (the FEA viewport, below its tool strip, or the CFD / CAD pane): dialogs
 * open there so they never cover the FEA browser columns. Hidden workspaces have an empty rectangle and are skipped.
 */
function viewportCorner() {
  const el = [...document.querySelectorAll("[data-dialog-anchor]")].find((e) => e.getBoundingClientRect().width > 0);
  const r = el?.getBoundingClientRect();
  if (!r) return { x: 312, y: 160 };
  const fea = el.classList.contains("viewport");
  return { x: Math.round(r.left + 12 + (fea ? 0 : Math.max(0, r.width - 380 - 24))), y: Math.round(r.top + (fea ? 42 : 12)) };
}

/**
 * Floating command dialog for a ribbon command (like a Fusion 360 command dialog). It does not block the rest of the
 * workspace, so the viewport stays usable (picking nodes / faces while the Selection dialog is open). The dialog stays
 * mounted while closed so half-filled forms survive closing and reopening. Drag it by its title bar; Esc closes it.
 */
export default function CommandModal({ open, title, icon, onClose, children, width = 340 }) {
  const [pos, setPos] = useState(viewportCorner);
  const drag = useRef(null);
  const moved = useRef(false);   // once dragged, the dialog stays where the user put it
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    if (!moved.current) setPos(viewportCorner());
    const onKey = (e) => { if (e.key === "Escape" && !e.defaultPrevented) onClose(); };
    window.addEventListener("keydown", onKey);
    boxRef.current?.focus({ preventScroll: true });
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const onDown = (e) => {
    if (e.button !== 0 || e.target.closest("button")) return;
    drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y };
    moved.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onMove = (e) => {
    if (!drag.current) return;
    const maxX = window.innerWidth - 120;
    const maxY = window.innerHeight - 40;
    setPos({ x: Math.min(maxX, Math.max(0, e.clientX - drag.current.dx)), y: Math.min(maxY, Math.max(0, e.clientY - drag.current.dy)) });
  };
  const onUp = () => { drag.current = null; };

  return (
    <section ref={boxRef} className="cmd-modal" role="dialog" aria-label={title} hidden={!open} tabIndex={-1}
      style={{ left: pos.x, top: pos.y, width, maxHeight: `calc(100vh - ${pos.y + 12}px)` }}>
      <header className="cmd-modal-head" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}>
        {icon && <Icon name={icon} size={16} />}
        <strong>{title}</strong>
        <span className="spacer" />
        <button className="cmd-modal-close" onClick={onClose} title="Close (Esc)" aria-label="Close"><Icon name="close" size={14} /></button>
      </header>
      <div className="cmd-modal-body">{children}</div>
    </section>
  );
}
