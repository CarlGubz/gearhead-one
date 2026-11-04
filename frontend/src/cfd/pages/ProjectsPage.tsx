import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Modal, StatusChip, Toast, timeAgo } from "../components/ui";
import type { ProjectListItem, TemplateInfo } from "../types";

/** The CFD cases of the open Gearhead project (each project can hold several cases next to its FEA models and CAD). */
export default function ProjectsPage({ projectId, projectName }: { projectId: string; projectName?: string }) {
  const nav = useNavigate();
  const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [creating, setCreating] = useState<TemplateInfo | null>(null);
  const [name, setName] = useState("");
  const [toast, setToast] = useState<{ m: string; err?: boolean } | null>(null);

  const load = () => api.projects(projectId).then(setProjects).catch((e) => setToast({ m: e.message, err: true }));
  useEffect(() => {
    void load();
    api.templates().then(setTemplates).catch(() => undefined);
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const create = async () => {
    if (!creating || !name.trim()) return;
    try {
      const p = await api.createProject({ name: name.trim(), template: creating.id === "blank" ? null : creating.id, description: creating.id === "blank" ? "" : undefined, project_id: projectId });
      nav(`/app/cfd/${p.id}/setup`);
    } catch (e) { setToast({ m: (e as Error).message, err: true }); }
  };

  return (
    <div className="container">
      <div className="hero">
        <div>
          <h1>CFD cases{projectName ? <span className="muted"> · {projectName}</span> : null}</h1>
          <p>Set up a flow problem, run the solver in the background, and explore the results — all in the browser. Start from a validated example or a blank channel, or send an FEA model or CAD part here with <b>Transfer</b> in the ribbon.</p>
        </div>
        <button className="btn primary" onClick={() => { setCreating(templates.find((t) => t.id === "blank") ?? { id: "blank", name: "Blank case", description: "" }); setName(""); }}>＋ New case</button>
      </div>

      {projects === null ? <span className="spin" /> : projects.length === 0 ? (
        <div className="empty">No CFD cases in this project yet — pick an example below to get started in under a minute, or use Transfer to flow around an FEA model or CAD part.</div>
      ) : (
        <div className="cards">
          {projects.map((p) => (
            <div key={p.id} className="card pcard" onClick={() => nav(`/app/cfd/${p.id}/setup`)}>
              <div className="row between"><span className="title">{p.name}</span>{p.last_run_status && <StatusChip status={p.last_run_status} />}</div>
              <div className="desc">{p.description || "No description"}</div>
              <div className="meta">
                <span className="chip">{p.dimension === "3d" ? `3-D ${p.nx}×${p.ny}×${p.nz}` : `${p.nx}×${p.ny}`} cells</span>
                {p.backend && p.backend !== "native" && <span className="chip">{p.backend}</span>}
                <span className="chip">{p.mode}</span>
                {p.energy && <span className="chip">thermal</span>}
                <span className="chip">{p.run_count} run{p.run_count === 1 ? "" : "s"}</span>
              </div>
              <div className="row between small muted">
                <span>edited {timeAgo(p.updated_at)}</span>
                <span className="row" onClick={(e) => e.stopPropagation()}>
                  <button className="btn sm ghost" onClick={async () => { await api.duplicateProject(p.id); void load(); setToast({ m: "Case duplicated" }); }}>Duplicate</button>
                  <button className="btn sm ghost danger" onClick={async () => {
                    if (!confirm(`Delete “${p.name}” and all of its runs?`)) return;
                    try { await api.deleteProject(p.id); void load(); } catch (e) { setToast({ m: (e as Error).message, err: true }); }
                  }}>Delete</button>
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="section-title"><h2>Examples</h2><span className="muted small">Benchmarks with published reference data</span></div>
      <div className="tcards">
        {templates.filter((t) => t.id !== "blank").map((t) => (
          <button key={t.id} className="card tcard" onClick={() => { setCreating(t); setName(t.name); }}>
            <b>{t.name}</b>
            <span className="muted small">{t.description}</span>
          </button>
        ))}
      </div>

      {creating && (
        <Modal title={creating.id === "blank" ? "New CFD case" : `New case from “${creating.name}”`} onClose={() => setCreating(null)}>
          {creating.id !== "blank" && <p className="muted small">{creating.description}</p>}
          <div className="field"><label>Case name</label><input type="text" autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void create()} /></div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => setCreating(null)}>Cancel</button>
            <button className="btn primary" disabled={!name.trim()} onClick={() => void create()}>Create</button>
          </div>
        </Modal>
      )}
      {toast && <Toast message={toast.m} error={toast.err} onDone={() => setToast(null)} />}
    </div>
  );
}
