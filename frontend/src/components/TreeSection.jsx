import React, { useState } from "react";
import { TreeBox } from "./BrowserPanel.jsx";
import Icon from "./icons.jsx";

/**
 * A tree node whose body is a form (analysis type, supports & loads, ...), drawn like the other tree rows:
 * [+]/[−] box, icon, title, optional "(count)" or grey badge text. The header click toggles it (the Guide relies on that).
 */
export default function TreeSection({ icon, title, badge, defaultOpen = true, tour, children }) {
  const [open, setOpen] = useState(defaultOpen);
  const count = typeof badge === "number";

  return (
    <div className="tree-section" data-tour={tour}>
      <div className="tree-section-header" onClick={() => setOpen((o) => !o)}>
        <TreeBox open={open} onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }} />
        {icon && <Icon name={icon} size={14} />}
        <span className="tree-section-title">{title}{count ? ` (${badge})` : ""}</span>
        {!count && badge !== undefined && badge !== null && <span className="tnode-meta">{badge}</span>}
      </div>
      {open && <div className="tree-section-body">{children}</div>}
    </div>
  );
}
