import React, { useRef, useState } from "react";

/**
 * Project command (all workspaces). A Gearhead project holds the FEA model stack, the CFD cases and the CAD documents;
 * Save writes them all into one .gearhead.json file (format version 3). Open creates a new project from a file, Open
 * into this project adds a file's FEA models, CFD cases and CAD documents to the open project. Older .gfea.json files
 * (FEA only) open too.
 */
export default function ProjectPanel({ projects, activeId, onSelect, onCreate, onDelete, onSave, onOpen, onMerge, onExportInp,
  history, onUndo, onRedo, workspace = "fea", summary }) {
  const [name, setName] = useState("New Project");
  const fileRef = useRef(null);
  const mergeRef = useRef(null);

  return (
    <>
      <div className="field-row">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Project name" />
        <button onClick={() => onCreate(name)}>+ New</button>
      </div>
      <ul className="project-list">
        {projects.map((p) => (
          <li key={p.id} className={p.id === activeId ? "active" : ""}>
            <span onClick={() => onSelect(p.id)}>{p.name}</span>
            <button className="link" title="Delete project (FEA models, CFD cases and CAD documents)"
              onClick={() => window.confirm(`Delete “${p.name}” with its FEA models, CFD cases and CAD documents?`) && onDelete(p.id)}>✕</button>
          </li>
        ))}
      </ul>
      {summary && (
        <p className="hint" style={{ marginTop: 6 }}>
          This project: {summary.fea.models} FEA model{summary.fea.models === 1 ? "" : "s"} · {summary.cfd.cases.length} CFD case
          {summary.cfd.cases.length === 1 ? "" : "s"} · {summary.cad.parts} CAD part{summary.cad.parts === 1 ? "" : "s"}
          {summary.cad.assemblies ? `, ${summary.cad.assemblies} assembl${summary.cad.assemblies === 1 ? "y" : "ies"}` : ""}
          {summary.cad.drawings ? `, ${summary.cad.drawings} drawing${summary.cad.drawings === 1 ? "" : "s"}` : ""}
        </p>
      )}
      {workspace === "fea" && (
        <div className="btn-row" style={{ marginTop: 8 }}>
          <button onClick={onUndo} disabled={!history?.canUndo}
            title={history?.canUndo ? `Undo: ${history.undoLabel} (Ctrl+Z)` : "Nothing to undo"}>↶ Undo</button>
          <button onClick={onRedo} disabled={!history?.canRedo}
            title={history?.canRedo ? `Redo: ${history.redoLabel} (Ctrl+Y)` : "Nothing to redo"}>↷ Redo</button>
        </div>
      )}
      <div className="btn-row">
        <button onClick={onSave} disabled={!activeId} title="Download the whole project (FEA, CFD and CAD) as one .gearhead.json file">Save…</button>
        <button onClick={() => fileRef.current?.click()} title="Open a saved .gearhead.json (or older .gfea.json) file as a new project">Open…</button>
      </div>
      <div className="btn-row">
        <button onClick={() => mergeRef.current?.click()} disabled={!activeId}
          title="Add the FEA models, CFD cases and CAD documents of a project file to this project">Open into this project…</button>
        {workspace === "fea" && <button onClick={onExportInp} disabled={!activeId} title="Export a CalculiX/Abaqus-style .inp deck of the active FEA model">Export .inp</button>}
      </div>
      <input ref={fileRef} type="file" accept=".json,application/json" style={{ display: "none" }}
        onChange={(e) => { if (e.target.files[0]) onOpen(e.target.files[0]); e.target.value = ""; }} />
      <input ref={mergeRef} type="file" accept=".json,application/json" style={{ display: "none" }}
        onChange={(e) => { if (e.target.files[0]) onMerge?.(e.target.files[0]); e.target.value = ""; }} />
    </>
  );
}
