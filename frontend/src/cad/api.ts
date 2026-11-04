import type {
  Assembly, CamSetup, Component, Constraint, Drawing, DrawingRender, Entity, Json, Mate, Meta, Part, Project, SimJob, Sketch, SolveResult,
} from "./types";

// Paths below are absolute (/api/cad/...). In the Gearhead app VITE_API_URL is the FEA client's base *path* ("/api"),
// so it must not be prefixed here; VITE_API_ORIGIN (e.g. https://api.example.com) is for an API on another origin.
const BASE = (import.meta as Json).env?.VITE_API_ORIGIN || "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T = Json>(method: string, path: string, body?: Json, form?: FormData): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* ignore */ }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T = Json>(p: string) => req<T>("GET", p);
const post = <T = Json>(p: string, b: Json = {}) => req<T>("POST", p, b);
const patch = <T = Json>(p: string, b: Json) => req<T>("PATCH", p, b);
const put = <T = Json>(p: string, b: Json) => req<T>("PUT", p, b);
const del = (p: string) => req("DELETE", p);

// for download links; the workspaces still write the stand-alone CADWeb paths (/api/parts/...), which live under /api/cad
export const url = (p: string) => BASE + (p.startsWith("/api/") && !p.startsWith("/api/cad/") ? "/api/cad/" + p.slice(5) : p);

export const api = {
  meta: () => get<Meta>("/api/cad/meta"),
  // projects
  projects: () => get<Project[]>("/api/cad/projects"),
  createProject: (name: string, description = "") => post<Project>("/api/cad/projects", { name, description }),
  createDemo: () => post<Project>("/api/cad/projects/demo"),
  /** add the demo content (plate, pins, assembly, CAM setup) to an existing CAD project */
  loadDemoInto: (id: number) => post<Project>(`/api/cad/projects/${id}/demo`),
  deleteProject: (id: number) => del(`/api/cad/projects/${id}`),
  project: (id: number) => get<Json>(`/api/cad/projects/${id}`),
  // sketches
  sketches: (pid: number) => get<Sketch[]>(`/api/cad/projects/${pid}/sketches`),
  createSketch: (pid: number, s: Partial<Sketch>) => post<Sketch>(`/api/cad/projects/${pid}/sketches`, s),
  updateSketch: (id: number, s: Partial<Sketch>) => put<Sketch>(`/api/cad/sketches/${id}`, s),
  deleteSketch: (id: number) => del(`/api/cad/sketches/${id}`),
  solvePreview: (entities: Entity[], constraints: Constraint[]) =>
    post<SolveResult>("/api/cad/solve-preview", { entities, constraints }),
  solveSketch: (id: number) => post<SolveResult & { sketch: Sketch }>(`/api/cad/sketches/${id}/solve`),
  analyseSketch: (id: number) => get<Json>(`/api/cad/sketches/${id}/analysis`),
  sketchOp: (id: number, op: string, params: Json, ids?: string[], replace = false) =>
    post<Sketch>(`/api/cad/sketches/${id}/ops`, { op, params, ids, replace }),
  sketchEdit: (id: number, op: string, params: Json, ids?: string[]) =>
    post<Sketch & { solve: { status: string; dof: number } | null }>(`/api/cad/sketches/${id}/edit`, { op, params, ids }),
  inferPreview: (entities: Entity[], constraints: Constraint[], ids?: string[]) =>
    post<{ constraints: Constraint[] }>("/api/cad/sketch-infer", { entities, constraints, ids }),
  inferSketch: (id: number) => post<Sketch & { added: number }>(`/api/cad/sketches/${id}/infer`),
  importDxf: (pid: number, file: File, plane: string) => {
    const f = new FormData();
    f.append("file", file);
    return req<Sketch>("POST", `/api/cad/projects/${pid}/sketches/import-dxf?plane=${plane}`, undefined, f);
  },
  // parts
  parts: (pid: number) => get<Part[]>(`/api/cad/projects/${pid}/parts`),
  createPart: (pid: number, name: string, material = "steel") => post<Part>(`/api/cad/projects/${pid}/parts`, { name, material }),
  updatePart: (id: number, p: Partial<Part>) => put<Part>(`/api/cad/parts/${id}`, p),
  deletePart: (id: number) => del(`/api/cad/parts/${id}`),
  duplicatePart: (id: number) => post<Part>(`/api/cad/parts/${id}/duplicate`),
  addFeature: (id: number, type: string, params: Json = {}, index?: number) =>
    post<Part>(`/api/cad/parts/${id}/features`, { type, params, index }),
  updateFeature: (id: number, fid: string, body: Json) => patch<Part>(`/api/cad/parts/${id}/features/${fid}`, body),
  deleteFeature: (id: number, fid: string) => req<Part>("DELETE", `/api/cad/parts/${id}/features/${fid}`),
  moveFeature: (id: number, fid: string, index: number) => post<Part>(`/api/cad/parts/${id}/features/${fid}/move`, { index }),
  partMesh: (id: number) => get<Json>(`/api/cad/parts/${id}/mesh`),
  partReference: (id: number, kind: "face" | "edge", indices: number[]) =>
    get<{ selector: string }>(`/api/cad/parts/${id}/reference?kind=${kind}&indices=${indices.join(",")}`),
  stabilizeRefs: (id: number) => post<Part & { changed: number }>(`/api/cad/parts/${id}/stabilize-references`),
  importStep: (pid: number, file: File) => {
    const f = new FormData();
    f.append("file", file);
    return req<Part>("POST", `/api/cad/projects/${pid}/parts/import-step`, undefined, f);
  },
  // assemblies
  assemblies: (pid: number) => get<Assembly[]>(`/api/cad/projects/${pid}/assemblies`),
  createAssembly: (pid: number, name: string) => post<Assembly>(`/api/cad/projects/${pid}/assemblies`, { name }),
  deleteAssembly: (id: number) => del(`/api/cad/assemblies/${id}`),
  assembly: (id: number) => get<Assembly>(`/api/cad/assemblies/${id}`),
  addComponent: (aid: number, part_id: number, position: number[] = [0, 0, 0]) =>
    post<Component>(`/api/cad/assemblies/${aid}/components`, { part_id, position }),
  updateComponent: (cid: number, body: Json) => patch<Component>(`/api/cad/components/${cid}`, body),
  deleteComponent: (cid: number) => del(`/api/cad/components/${cid}`),
  addMate: (aid: number, m: Partial<Mate>) => post<Mate>(`/api/cad/assemblies/${aid}/mates`, m),
  deleteMate: (mid: number) => del(`/api/cad/mates/${mid}`),
  solveAssembly: (aid: number) => post<Json>(`/api/cad/assemblies/${aid}/solve`),
  assemblyMesh: (aid: number) => get<Json>(`/api/cad/assemblies/${aid}/mesh`),
  interference: (aid: number) => get<Json>(`/api/cad/assemblies/${aid}/interference`),
  bom: (aid: number) => get<Json>(`/api/cad/assemblies/${aid}/bom`),
  // drawings
  drawings: (pid: number) => get<Drawing[]>(`/api/cad/projects/${pid}/drawings`),
  createDrawing: (pid: number, body: Json) => post<Drawing>(`/api/cad/projects/${pid}/drawings`, body),
  updateDrawing: (id: number, body: Partial<Drawing>) => patch<Drawing>(`/api/cad/drawings/${id}`, body),
  deleteDrawing: (id: number) => del(`/api/cad/drawings/${id}`),
  addView: (id: number, body: Json) => post<Drawing>(`/api/cad/drawings/${id}/views`, body),
  standardViews: (id: number, part_id: number) => post<Drawing>(`/api/cad/drawings/${id}/standard-views`, { part_id }),
  addDimension: (id: number, body: Json) => post<Drawing>(`/api/cad/drawings/${id}/dimensions`, body),
  autoDimensions: (id: number) => post<Drawing & { added: number }>(`/api/cad/drawings/${id}/auto-dimensions`),
  renderDrawing: (id: number) => get<DrawingRender>(`/api/cad/drawings/${id}/render`),
  // cam
  camSetups: (partId: number) => get<CamSetup[]>(`/api/cad/parts/${partId}/cam`),
  createCam: (partId: number, name: string) => post<CamSetup>(`/api/cad/parts/${partId}/cam`, { name }),
  camSetup: (id: number) => get<CamSetup>(`/api/cad/cam/${id}`),
  updateCam: (id: number, body: Json) => patch<CamSetup>(`/api/cad/cam/${id}`, body),
  deleteCam: (id: number) => del(`/api/cad/cam/${id}`),
  camAuto: (id: number) => post<CamSetup>(`/api/cad/cam/${id}/auto-operations`),
  camGenerate: (id: number) => post<Json>(`/api/cad/cam/${id}/generate`),
  camSimulate: (id: number) => post<Json>(`/api/cad/cam/${id}/simulate`),
  // simulation
  startSim: (partId: number, kind: string, config: Json) => post<SimJob>(`/api/cad/parts/${partId}/simulations`, { kind, config }),
  sim: (id: number) => get<SimJob>(`/api/cad/simulations/${id}`),
  sims: (partId: number) => get<SimJob[]>(`/api/cad/parts/${partId}/simulations`),
  startAsmSim: (aid: number, kind: string, config: Json) => post<SimJob>(`/api/cad/assemblies/${aid}/simulations`, { kind, config }),
  asmSims: (aid: number) => get<SimJob[]>(`/api/cad/assemblies/${aid}/simulations`),
  calculix: (refresh = false) => get<Json>(`/api/cad/calculix?refresh=${refresh}`),
};
