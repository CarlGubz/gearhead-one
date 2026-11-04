import { useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes, useParams } from "react-router-dom";
import { api } from "./api";
import { ProjectProvider, useProject } from "./ProjectContext";
import type { Project } from "./types";
import ProjectsPage from "./pages/ProjectsPage";
import SetupPage from "./pages/SetupPage";
import RunsPage from "./pages/RunsPage";
import ResultsPage from "./pages/ResultsPage";

/**
 * The CFD workspace of the Gearhead app (formerly the CFDFlow front end). It is rendered by the Workspace page inside
 * a shadow root (see components/ShadowPane.jsx), under the route /app/*:
 *   /app/cfd                      cases of the open Gearhead project
 *   /app/cfd/:pid/setup           ① Setup        (BaramFlow-style sections, geometry canvas, live case check)
 *   /app/cfd/:pid/runs[/:rid]     ② Run & monitor
 *   /app/cfd/:pid/results[/:rid]  ③ Results
 * The app bar, ribbon, account and theme belong to the Gearhead shell; this component only draws the workspace body.
 */
function Frame({ children, crumb }: { children: ReactNode; crumb?: ReactNode }) {
  return (
    <div className="app">
      {crumb && <header className="topbar cfd-subbar">{crumb}</header>}
      <div className="page" style={{ overflow: "hidden" }}>{children}</div>
    </div>
  );
}

function CaseFrame({ projectId, onCase }: { projectId: string; onCase?: (c: Project | null) => void }) {
  const { pid } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setProject(null);
    setError(null);
    api.project(pid!).then(setProject).catch((e) => setError(e.message));
  }, [pid]);
  useEffect(() => { onCase?.(project); return () => onCase?.(null); }, [project, onCase]);
  if (error) return <Frame><div className="container"><div className="banner err">{error} — <Link to="/app/cfd">back to the cases</Link></div></div></Frame>;
  if (!project) return <Frame><div className="container"><span className="spin" /> Loading…</div></Frame>;
  // the case belongs to another Gearhead project (the user switched project): show this project's cases
  if (project.project_id && project.project_id !== projectId) return <Navigate to="/app/cfd" replace />;
  return (
    <ProjectProvider key={project.id} project={project}>
      <CaseShell />
    </ProjectProvider>
  );
}

function CaseShell() {
  const { project, save, check } = useProject();
  const errors = check?.items.filter((i) => i.level === "error").length ?? 0;
  const crumb = (
    <>
      <span className="crumb"><Link to="/app/cfd">Cases</Link> / <strong>{project.name}</strong></span>
      <nav className="tabs">
        <NavLink to="setup" className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>① Setup{errors > 0 && <span className="chip failed" style={{ padding: "0 6px" }}>{errors}</span>}</NavLink>
        <NavLink to="runs" className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>② Run &amp; monitor</NavLink>
        <NavLink to="results" className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>③ Results</NavLink>
      </nav>
      <span className="crumb" style={{ marginLeft: 8 }}>
        {save.state === "saving" && <><span className="spin" /> saving…</>}
        {save.state === "dirty" && "unsaved changes"}
        {save.state === "saved" && "✓ saved"}
        {save.state === "error" && <span style={{ color: "var(--err)" }}>⚠ not saved</span>}
      </span>
    </>
  );
  return <Frame crumb={crumb}><Outlet /></Frame>;
}

export default function CfdWorkspace({ projectId, projectName, onCase }: {
  projectId: string;
  projectName?: string;
  /** the open case (null on the cases list) - the shell shows it in the app bar and uses it for Transfer */
  onCase?: (c: Project | null) => void;
}) {
  return (
    <Routes>
      <Route path="cfd" element={<Frame><div className="page" style={{ height: "100%", overflow: "auto" }}><ProjectsPage projectId={projectId} projectName={projectName} /></div></Frame>} />
      <Route path="cfd/:pid" element={<CaseFrame projectId={projectId} onCase={onCase} />}>
        <Route index element={<Navigate to="setup" replace />} />
        <Route path="setup" element={<SetupPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:rid" element={<RunsPage />} />
        <Route path="results" element={<ResultsPage />} />
        <Route path="results/:rid" element={<ResultsPage />} />
      </Route>
      <Route path="cfd/*" element={<Navigate to="/app/cfd" replace />} />
    </Routes>
  );
}
