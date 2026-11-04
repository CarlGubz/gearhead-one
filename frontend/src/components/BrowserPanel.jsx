import React, { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import Icon from "./icons.jsx";

/** Text typed in the side panel's search box; while it is set every tree node renders expanded so it can be matched. */
export const TreeSearch = createContext("");

const TAB_KEY = "webfea-side-tab";

/**
 * Hide every tree node (and tree section) whose label does not contain `q` and that has no matching descendant.
 * Works on the rendered DOM so it covers every tree in the panel, whatever component drew it.
 */
function applyFilter(root, q) {
  const nodes = root.querySelectorAll(".tnode, .tree-section");
  if (!q) {
    nodes.forEach((n) => { n.style.display = ""; });
    return;
  }
  const own = (n) => (n.querySelector(":scope > .tnode-row .tnode-text, :scope > .tree-section-header .tree-section-title")?.textContent || "").toLowerCase();
  const memo = new Map();
  const visible = (n) => {
    if (memo.has(n)) return memo.get(n);
    let v = own(n).includes(q);
    if (!v) n.querySelectorAll(".tnode, .tree-section").forEach((d) => { if (!v && own(d).includes(q)) v = true; });
    memo.set(n, v);
    return v;
  };
  // shown: matches itself, holds a match, or sits under a node that matches (searching "Parts" lists every part)
  const underMatch = (n) => {
    for (let p = n.parentElement?.closest(".tnode, .tree-section"); p; p = p.parentElement?.closest(".tnode, .tree-section")) {
      if (own(p).includes(q)) return true;
    }
    return false;
  };
  nodes.forEach((n) => { n.style.display = visible(n) || underMatch(n) ? "" : "none"; });
}

/**
 * PrePoMax-style side panel: tabs across the top (Geometry | Model | Analysis), a search box, then the active tab's
 * tree. All tabs stay mounted (forms keep their state, the Guide can find sections); only the active one is shown.
 * tabs: [{ id, label, icon, tours: [data-tour ids inside the tab], content }]
 */
export default function SidePanel({ tabs }) {
  const [tab, setTab] = useState(() => {
    try { return localStorage.getItem(TAB_KEY) || tabs[0].id; } catch (e) { return tabs[0].id; }
  });
  const [query, setQuery] = useState("");
  const bodyRef = useRef(null);
  const q = query.trim().toLowerCase();

  useEffect(() => { try { localStorage.setItem(TAB_KEY, tab); } catch (e) { /* storage unavailable */ } }, [tab]);

  // the Guide / links point at a section by its data-tour id: show the tab that holds it
  const tourMap = JSON.stringify(tabs.map((t) => [t.id, t.tours || []]));
  useEffect(() => {
    const map = JSON.parse(tourMap);
    const onOpen = (e) => { const hit = map.find(([, tours]) => tours.includes(e.detail)); if (hit) setTab(hit[0]); };
    window.addEventListener("gh:open-command", onOpen);
    return () => window.removeEventListener("gh:open-command", onOpen);
  }, [tourMap]);

  useLayoutEffect(() => { if (bodyRef.current) applyFilter(bodyRef.current, q); });

  return (
    <aside className="side-panel" data-tour="side-panel">
      <div className="ptabs" role="tablist">
        {tabs.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`ptab ${tab === t.id ? "active" : ""}`}
            data-tour={`tab-${t.id}`} onClick={() => setTab(t.id)}>
            {t.icon && <Icon name={t.icon} size={14} />}{t.label}
          </button>
        ))}
      </div>
      <div className="psearch">
        <Icon name="search" size={14} />
        <input type="search" value={query} placeholder="Search the tree" aria-label="Search the tree"
          onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setQuery("")} />
      </div>
      <TreeSearch.Provider value={q}>
        <div className="pbody" ref={bodyRef}>
          {tabs.map((t) => <div key={t.id} role="tabpanel" hidden={tab !== t.id} className="ptab-body">{t.content}</div>)}
        </div>
      </TreeSearch.Provider>
    </aside>
  );
}

/** Small square icon button used in panel toolbars and tree rows. */
export function IconBtn({ icon, title, onClick, disabled, active = false, size = 14 }) {
  return (
    <button className={`icon-btn ${active ? "active" : ""}`} title={title} aria-label={title} onClick={onClick} disabled={disabled}>
      <Icon name={icon} size={size} />
    </button>
  );
}

/** The [+] / [−] box of an expandable tree row. */
export function TreeBox({ open, onClick }) {
  return (
    <button className="tbox" onClick={onClick} aria-expanded={open} aria-label={open ? "Collapse" : "Expand"}>
      {open ? "−" : "+"}
    </button>
  );
}

/**
 * One node of a PrePoMax-style tree: [+]/[−] box, icon, label, optional "(count)" and grey meta text. A node with
 * children is expandable; while the panel search box has text every node renders open so the filter can see it.
 */
export function TreeNode({ label, count, meta, icon, defaultOpen = true, children, onClick, active = false, right, title }) {
  const [open, setOpen] = useState(defaultOpen);
  const searching = !!useContext(TreeSearch);
  const leaf = !React.Children.toArray(children).some(Boolean);
  const shown = open || searching;
  return (
    <li className={`tnode ${active ? "active" : ""} ${leaf ? "leaf" : ""}`}>
      <div className="tnode-row">
        {leaf ? <span className="tbox-spacer" /> : <TreeBox open={shown} onClick={() => setOpen(!shown)} />}
        <button className="tnode-label" title={title} onClick={onClick || (leaf ? undefined : () => setOpen(!shown))}
          disabled={!onClick && leaf}>
          {icon && <Icon name={icon} size={14} />}
          <span className="tnode-text">{label}{count !== undefined && count !== null ? ` (${count})` : ""}</span>
          {meta !== undefined && meta !== null && meta !== "" && <span className="tnode-meta">{meta}</span>}
        </button>
        {right}
      </div>
      {!leaf && shown && <ul className="tree">{children}</ul>}
    </li>
  );
}
