import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";
import type { CaseConfig, CheckResult, Project } from "./types";

export type SaveState = { state: "saved" | "dirty" | "saving" | "error"; errors: string[] };

interface Ctx {
  project: Project;
  config: CaseConfig;
  setConfig: (fn: (c: CaseConfig) => CaseConfig) => void;
  rename: (name: string, description: string) => void;
  save: SaveState;
  flush: () => Promise<boolean>;
  check: CheckResult | null;
}

const C = createContext<Ctx | null>(null);
export const useProject = () => {
  const v = useContext(C);
  if (!v) throw new Error("useProject outside provider");
  return v;
};

export function ProjectProvider({ project, children }: { project: Project; children: ReactNode }) {
  const [config, setCfg] = useState<CaseConfig>(project.config);
  const [meta, setMeta] = useState({ name: project.name, description: project.description });
  const [save, setSave] = useState<SaveState>({ state: "saved", errors: [] });
  const [check, setCheck] = useState<CheckResult | null>(null);
  const dirty = useRef(false);
  const latest = useRef({ config, meta });
  latest.current = { config, meta };
  const timer = useRef<number | undefined>(undefined);
  const inflight = useRef<Promise<boolean> | null>(null);

  const doSave = useCallback(async (): Promise<boolean> => {
    if (!dirty.current) return inflight.current ? inflight.current : true;
    dirty.current = false;
    setSave((s) => ({ ...s, state: "saving" }));
    const { config: c, meta: m } = latest.current;
    const p = api
      .updateProject(project.id, { name: m.name, description: m.description, config: c })
      .then(() => {
        setSave((s) => ({ state: dirty.current ? "dirty" : "saved", errors: dirty.current ? s.errors : [] }));
        return true;
      })
      .catch((e: unknown) => {
        dirty.current = true;
        const err = e instanceof ApiError ? e : new ApiError(0, String(e));
        setSave({ state: "error", errors: err.details.length ? err.details : [err.message] });
        return false;
      });
    inflight.current = p;
    const ok = await p;
    inflight.current = null;
    return ok;
  }, [project.id]);

  const schedule = useCallback(() => {
    dirty.current = true;
    setSave((s) => ({ ...s, state: s.state === "error" ? "error" : "dirty" }));
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void doSave(), 600);
  }, [doSave]);

  const setConfig = useCallback((fn: (c: CaseConfig) => CaseConfig) => {
    setCfg((c) => fn(c));
    schedule();
  }, [schedule]);

  const rename = useCallback((name: string, description: string) => {
    setMeta({ name, description });
    schedule();
  }, [schedule]);

  // flush pending edits when leaving the project
  useEffect(() => () => {
    window.clearTimeout(timer.current);
    if (dirty.current) {
      const { config: c, meta: m } = latest.current;
      void fetch(`/api/cfd/projects/${project.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, keepalive: true,
        body: JSON.stringify({ name: m.name, description: m.description, config: c }),
      });
    }
  }, [project.id]);

  // debounced live case check
  useEffect(() => {
    const ctl = new AbortController();
    const t = window.setTimeout(() => {
      api.check(config, ctl.signal).then(setCheck).catch(() => undefined);
    }, 350);
    return () => { window.clearTimeout(t); ctl.abort(); };
  }, [config]);

  const flush = useCallback(async () => {
    window.clearTimeout(timer.current);
    return doSave();
  }, [doSave]);

  const value = useMemo<Ctx>(
    () => ({ project: { ...project, name: meta.name, description: meta.description }, config, setConfig, rename, save, flush, check }),
    [project, meta, config, setConfig, rename, save, flush, check],
  );
  return <C.Provider value={value}>{children}</C.Provider>;
}
