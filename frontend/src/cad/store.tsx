import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type { Meta, Part, Project, Sketch } from "./types";

export type Tab = "sketch" | "model" | "assembly" | "drawing" | "cam" | "simulate";

interface Ctx {
  meta: Meta | null;
  projects: Project[];
  project: Project | null;
  sketches: Sketch[];
  parts: Part[];
  tab: Tab;
  setTab: (t: Tab) => void;
  selectProject: (id: number) => void;
  refreshProjects: () => Promise<Project[]>;
  refreshSketches: () => Promise<Sketch[]>;
  refreshParts: () => Promise<Part[]>;
  upsertSketch: (s: Sketch) => void;
  upsertPart: (p: Part) => void;
  /** part/sketch currently focused (shared across workspaces) */
  partId: number | null;
  setPartId: (id: number | null) => void;
  sketchId: number | null;
  setSketchId: (id: number | null) => void;
  notify: (msg: string, kind?: "err" | "ok") => void;
  toast: { msg: string; kind: "err" | "ok" } | null;
  clearToast: () => void;
  busy: string | null;
  run: <T>(label: string, fn: () => Promise<T>) => Promise<T | undefined>;
}

const C = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(C);

/**
 * In the Gearhead app the CAD workspace always shows the CAD project of the open Gearhead project (`cadProjectId`),
 * and its workspace tab (Sketch 2D … Simulate) is chosen in the ribbon (`tab` / `onTab`). Without those props the
 * provider behaves like the stand-alone CADWeb app (first project of the list, own tab state).
 */
export function AppProvider({ children, cadProjectId, tab: tabProp, onTab }: {
  children: ReactNode; cadProjectId?: number | null; tab?: Tab; onTab?: (t: Tab) => void;
}) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [sketches, setSketches] = useState<Sketch[]>([]);
  const [parts, setParts] = useState<Part[]>([]);
  const [tabState, setTabState] = useState<Tab>(tabProp ?? "model");
  const tab = tabProp ?? tabState;
  const setTab = useCallback((t: Tab) => { setTabState(t); onTab?.(t); }, [onTab]);
  const [partId, setPartId] = useState<number | null>(null);
  const [sketchId, setSketchId] = useState<number | null>(null);
  const [toast, setToast] = useState<Ctx["toast"]>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const notify = useCallback((msg: string, kind: "err" | "ok" = "err") => setToast({ msg, kind }), []);
  const clearToast = useCallback(() => setToast(null), []);

  const run = useCallback(async <T,>(label: string, fn: () => Promise<T>) => {
    setBusy(label);
    try {
      return await fn();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e));
      return undefined;
    } finally {
      setBusy(null);
    }
  }, [notify]);

  const projectsRef = useRef<Project[]>([]);
  const refreshProjects = useCallback(async () => {
    let p: Project[];
    if (cadProjectId != null) {
      const one = (await api.project(cadProjectId)) as Project;
      p = [{ id: one.id, name: one.name, description: one.description, counts: one.counts }];
    } else {
      p = await api.projects();
    }
    projectsRef.current = p; // synchronous copy so selectProject works right after a refresh
    setProjects(p);
    return p;
  }, [cadProjectId]);

  useEffect(() => {
    (async () => {
      try {
        setMeta(await api.meta());
        const p = await refreshProjects();
        if (p.length) setProject(p[0]);
      } catch (e) {
        notify("Cannot reach the API: " + (e instanceof Error ? e.message : e));
      }
    })();
  }, [refreshProjects, notify]);

  const refreshSketches = useCallback(async () => {
    if (!project) return [];
    const s = await api.sketches(project.id);
    setSketches(s);
    return s;
  }, [project]);
  const refreshParts = useCallback(async () => {
    if (!project) return [];
    const p = await api.parts(project.id);
    setParts(p);
    return p;
  }, [project]);

  useEffect(() => {
    setSketches([]); setParts([]); setPartId(null); setSketchId(null);
    if (!project) return;
    (async () => {
      try {
        const [s, p] = await Promise.all([api.sketches(project.id), api.parts(project.id)]);
        setSketches(s); setParts(p);
        setSketchId(s[0]?.id ?? null);
        setPartId(p[0]?.id ?? null);
      } catch (e) { notify(String(e)); }
    })();
  }, [project, notify]);

  const upsertSketch = useCallback((s: Sketch) => setSketches((l) => (l.some((x) => x.id === s.id) ? l.map((x) => (x.id === s.id ? s : x)) : [...l, s])), []);
  const upsertPart = useCallback((p: Part) => setParts((l) => (l.some((x) => x.id === p.id) ? l.map((x) => (x.id === p.id ? { ...x, ...p } : x)) : [...l, p])), []);
  const selectProject = useCallback((id: number) => setProject(projectsRef.current.find((p) => p.id === id) ?? null), []);

  const value = useMemo<Ctx>(() => ({
    meta, projects, project, sketches, parts, tab, setTab, selectProject, refreshProjects, refreshSketches, refreshParts,
    upsertSketch, upsertPart, partId, setPartId, sketchId, setSketchId, notify, toast, clearToast, busy, run,
  }), [meta, projects, project, sketches, parts, tab, setTab, selectProject, refreshProjects, refreshSketches, refreshParts, upsertSketch, upsertPart, partId, sketchId, notify, toast, clearToast, busy, run]);

  return <C.Provider value={value}>{children}</C.Provider>;
}
