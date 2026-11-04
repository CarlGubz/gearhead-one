import type {
  CaseConfig, CheckResult, FieldsPayload, LogLine, MonitorSample, Project, ProjectListItem, ProbeLine, Run, RunDetail, SolverInfo, TemplateInfo,
} from "./types";

/** 3-D results are viewed as slices: axis 0/1/2 = slice normal to x/y/z, index = cell layer. */
export interface Slice { axis: 0 | 1 | 2; index: number }
const sliceQuery = (s?: Slice | null) => (s ? `&axis=${s.axis}&index=${s.index}` : "");

export class ApiError extends Error {
  status: number;
  details: string[];
  constructor(status: number, message: string, details: string[] = []) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

function describe(detail: unknown): { message: string; details: string[] } {
  if (typeof detail === "string") return { message: detail, details: [] };
  if (Array.isArray(detail)) {
    const details = detail.map((d: { loc?: (string | number)[]; msg?: string }) => `${(d.loc ?? []).filter((p) => p !== "body" && p !== "config").join(" › ")}: ${d.msg ?? "invalid"}`);
    return { message: "Some values are invalid", details };
  }
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; errors?: string[] };
    return { message: d.message ?? "Request failed", details: d.errors ?? [] };
  }
  return { message: "Request failed", details: [] };
}

async function request<T>(method: string, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail; } catch { /* not json */ }
    const { message, details } = describe(detail);
    throw new ApiError(res.status, message, details);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  templates: () => request<TemplateInfo[]>("GET", "/api/cfd/templates"),
  solvers: () => request<SolverInfo[]>("GET", "/api/cfd/solvers"),
  exportCaseUrl: (pid: string, backend: string) => `/api/cfd/projects/${pid}/export/${backend}`,
  /** CFD cases of one Gearhead project (the integrated app always passes it). */
  projects: (projectId?: string) =>
    request<ProjectListItem[]>("GET", `/api/cfd/projects${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
  createProject: (b: { name: string; description?: string; template?: string | null; project_id?: string }) =>
    request<Project>("POST", "/api/cfd/projects", b),
  project: (id: string) => request<Project>("GET", `/api/cfd/projects/${id}`),
  updateProject: (id: string, b: { name?: string; description?: string; config?: CaseConfig }) => request<Project>("PUT", `/api/cfd/projects/${id}`, b),
  duplicateProject: (id: string) => request<Project>("POST", `/api/cfd/projects/${id}/duplicate`),
  deleteProject: (id: string) => request<void>("DELETE", `/api/cfd/projects/${id}`),
  check: (config: CaseConfig, signal?: AbortSignal) => request<CheckResult>("POST", "/api/cfd/check", config, signal),
  runs: (pid: string) => request<Run[]>("GET", `/api/cfd/projects/${pid}/runs`),
  createRun: (pid: string, name: string) => request<Run>("POST", `/api/cfd/projects/${pid}/runs`, { name }),
  run: (id: string) => request<RunDetail>("GET", `/api/cfd/runs/${id}`),
  cancelRun: (id: string) => request<Run>("POST", `/api/cfd/runs/${id}/cancel`),
  deleteRun: (id: string) => request<void>("DELETE", `/api/cfd/runs/${id}`),
  monitors: (id: string, since = -1) => request<{ samples: MonitorSample[]; stride: number }>("GET", `/api/cfd/runs/${id}/monitors?since=${since}`),
  logs: (id: string, after = 0) => request<LogLine[]>("GET", `/api/cfd/runs/${id}/logs?after=${after}`),
  frames: (id: string) => request<{ times: number[]; final: boolean }>("GET", `/api/cfd/runs/${id}/frames`),
  fields: (id: string, frame = -1, slice?: Slice | null) => request<FieldsPayload>("GET", `/api/cfd/runs/${id}/fields?frame=${frame}${sliceQuery(slice)}`),
  probeLine: (id: string, p0: [number, number], p1: [number, number], frame = -1, n = 160, slice?: Slice | null) =>
    request<ProbeLine>("GET", `/api/cfd/runs/${id}/probe-line?x0=${p0[0]}&y0=${p0[1]}&x1=${p1[0]}&y1=${p1[1]}&n=${n}&frame=${frame}${sliceQuery(slice)}`),
  exportUrl: (id: string, kind: "vtu" | "csv" | "monitors", frame = -1) => `/api/cfd/runs/${id}/export/${kind}?frame=${frame}`,
  streamUrl: (id: string, since: number, after: number) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/cfd/runs/${id}/stream?since=${since}&after=${after}`,
};
