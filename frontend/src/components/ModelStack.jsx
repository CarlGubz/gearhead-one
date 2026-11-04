import React, { useState } from "react";
import { IconBtn, TreeNode } from "./BrowserPanel.jsx";
import { ANALYSES } from "./AnalysisPanel.jsx";

// legacy per-model tags of the old CAD / CAM / CAE sub-workspaces of FEA (kept in files for compatibility)
const PROCESS_LABEL = { cad: "GEO", cam: "CAM", cae: "FEA" };

function LayerRow({ layer, onActivate, onRename, onToggle }) {
  const [editing, setEditing] = useState(false);
  const s = layer.summary || {};
  const stats = s.n_elements
    ? `${s.element_type} · ${s.n_elements.toLocaleString()} el · ${ANALYSES.find((a) => a.value === s.analysis_type)?.label.split(" (")[0] || s.analysis_type}`
    : "empty";
  return (
    <li className={`layer ${layer.active ? "active" : ""} ${layer.visible || layer.active ? "" : "hidden-layer"}`}
      onClick={() => !layer.active && !editing && onActivate(layer.id)} title={layer.active ? "Active model" : "Click to make this the active model"}>
      <IconBtn icon={layer.visible || layer.active ? "eye" : "eyeOff"} disabled={layer.active}
        title={layer.active ? "The active model is always shown" : layer.visible ? "Hide the reference ghost" : "Show as a reference ghost"}
        onClick={(e) => { e.stopPropagation(); onToggle(layer.id, !layer.visible); }} />
      <span className={`layer-process p-${layer.process}`}>{PROCESS_LABEL[layer.process] || "FEA"}</span>
      <span className="layer-main">
        {editing ? (
          <input type="text" defaultValue={layer.name} autoFocus maxLength={80} onClick={(e) => e.stopPropagation()}
            onBlur={(e) => { setEditing(false); const v = e.target.value.trim(); if (v && v !== layer.name) onRename(layer.id, v); }}
            onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") { e.preventDefault(); setEditing(false); } }} />
        ) : (
          <span className="layer-name" onDoubleClick={(e) => { e.stopPropagation(); setEditing(true); }} title="Double-click to rename">{layer.name}</span>
        )}
        <span className="layer-stats">{stats}{s.n_setup ? ` · ${s.n_setup} supports/loads` : ""}</span>
      </span>
    </li>
  );
}

/**
 * Model panel. A project holds a stack of models (meshes with their own materials, supports, loads, analysis, undo
 * history and results), ordered like image-editor layers: the newest on top. Clicking a model makes it the active one,
 * the one every command, the solver and the AI assistant work on. Visible inactive models are drawn as ghosts.
 * Below the stack: the active model's tree in PrePoMax's FE Model layout (mesh, features, materials, constraints, steps)
 * - items open the matching command or section - and its edit history.
 */
export default function ModelStack({
  project, busy, onActivate, onAdd, onDuplicate, onRename, onToggle, onMove, onDelete, onOpen, onSpotlight, onGoto,
}) {
  if (!project) return <p className="hint">Open or create a project first.</p>;
  const stack = project.layer_stack || [];
  const k = stack.findIndex((l) => l.active);
  const active = stack[k];
  const labels = project.history_labels || [];
  const pos = project.history_pos ?? -1;
  const parts = project.parts || [];
  const sets = project.node_sets || [];
  const refs = project.reference_points || [];
  const cons = project.constraints || [];
  const bcs = project.boundary_conditions || [];
  const loads = project.loads || [];
  const mats = [{ id: "default", name: project.material?.name || "Default" }, ...(project.materials || [])];

  return (
    <>
      <div className="layer-toolbar">
        <span className="layer-title">Models ({stack.length})</span>
        <IconBtn icon="plus" title="New empty model on top of the stack" onClick={onAdd} disabled={busy} />
        <IconBtn icon="duplicate" title="Duplicate the active model (mesh and setup, without results or history)"
          onClick={() => onDuplicate(active)} disabled={busy || !active} />
        <span className="vt-sep" />
        <IconBtn icon="up" title="Move the active model up" onClick={() => onMove(active.id, "up")} disabled={busy || k <= 0} />
        <IconBtn icon="down" title="Move the active model down" onClick={() => onMove(active.id, "down")}
          disabled={busy || k < 0 || k >= stack.length - 1} />
        <IconBtn icon="trash" title={stack.length > 1 ? "Delete the active model" : "A project needs at least one model"}
          onClick={() => onDelete(active)} disabled={busy || stack.length < 2} />
      </div>
      <ul className="layers" aria-label="Model stack">
        {stack.map((l) => <LayerRow key={l.id || "m"} layer={l} onActivate={onActivate} onRename={onRename} onToggle={onToggle} />)}
      </ul>

      <ul className="tree ptree">
        <TreeNode label={active ? `Model · ${active.name}` : "Model"} icon="layers">
          <TreeNode label="Mesh" icon="mesh">
            <TreeNode label="Parts" count={parts.length} icon="parts" defaultOpen={false}>
              {parts.map((p) => <TreeNode key={p.id} label={p.name} icon="parts" meta={`${p.elem_end - p.elem_start} el`} onClick={() => onOpen("parts")} />)}
            </TreeNode>
            <TreeNode label="Node Sets" count={sets.length} icon="sets" defaultOpen={false} onClick={sets.length ? undefined : () => onOpen("selection")}>
              {sets.map((n) => <TreeNode key={n.id} label={n.name} icon="sets" meta={`${n.ids?.length || 0} ${n.kind === "faces" ? "faces" : "nodes"}`} onClick={() => onOpen("selection")} />)}
            </TreeNode>
          </TreeNode>
          <TreeNode label="Features" icon="refpoint" defaultOpen={false}>
            <TreeNode label="Reference Points" count={refs.length} icon="refpoint" onClick={refs.length ? undefined : () => onSpotlight("constraints")}>
              {refs.map((r) => <TreeNode key={r.id} label={r.name || r.id} icon="refpoint" onClick={() => onSpotlight("constraints")} />)}
            </TreeNode>
          </TreeNode>
          <TreeNode label="Materials" count={mats.length} icon="materials" defaultOpen={false}>
            {mats.map((m) => <TreeNode key={m.id} label={m.name} icon="materials" meta={m.id === "default" ? "default" : null} onClick={() => onOpen("materials")} />)}
          </TreeNode>
          <TreeNode label="Constraints" count={cons.length} icon="constraints" defaultOpen={false} onClick={cons.length ? undefined : () => onSpotlight("constraints")}>
            {cons.map((c) => <TreeNode key={c.id} label={c.name || c.type} icon="constraints" meta={c.type} onClick={() => onSpotlight("constraints")} />)}
          </TreeNode>
          <TreeNode label="Steps" icon="step">
            <TreeNode label={`Step-1 · ${ANALYSES.find((a) => a.value === project.analysis_type)?.label.split(" (")[0] || project.analysis_type}`}
              icon="analysis" onClick={() => onSpotlight("analysis")}>
              <TreeNode label="Supports" count={bcs.length} icon="loads" defaultOpen={false} onClick={bcs.length ? undefined : () => onSpotlight("loads")}>
                {bcs.map((b) => <TreeNode key={b.id} label={b.name || "Support"} icon="loads" onClick={() => onSpotlight("loads")} />)}
              </TreeNode>
              <TreeNode label="Loads" count={loads.length} icon="loads" defaultOpen={false} onClick={loads.length ? undefined : () => onSpotlight("loads")}>
                {loads.map((l) => <TreeNode key={l.id} label={l.name || l.load_type} icon="loads" meta={(l.load_type || "").replace(/_/g, " ")} onClick={() => onSpotlight("loads")} />)}
              </TreeNode>
            </TreeNode>
          </TreeNode>
          <TreeNode label="History" count={labels.length} icon="history" defaultOpen={false}>
            {labels.length === 0 && <li className="tnode-note">No edits yet.</li>}
            {labels.map((l, i) => (
              <li key={i} className={`hist ${i === pos ? "current" : ""} ${i > pos ? "redo" : ""}`}>
                <button onClick={() => i !== pos && onGoto(i)} disabled={busy}
                  title={i === pos ? "Current state" : i < pos ? "Go back to this state" : "Redo up to this state"}>
                  <span className="hist-dot" />{l}
                </button>
              </li>
            ))}
          </TreeNode>
        </TreeNode>
      </ul>
    </>
  );
}
