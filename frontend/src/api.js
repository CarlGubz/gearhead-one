import axios from "axios";

// In dev, Vite proxies "/api" to the backend (see vite.config.js). In a
// production build you can bake in an absolute backend URL at build time
// with VITE_API_URL (e.g. https://api.yourdomain.com/api); otherwise it
// falls back to the relative "/api" path (works when both are served
// behind the same reverse proxy / domain).
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "/api",
  withCredentials: true, // session cookie
});

// Global reactions to account/plan errors so every screen behaves the same:
//  401 on a normal call  -> session expired, AuthProvider clears the user
//  402                   -> Free-plan limit hit, show the upgrade dialog
api.interceptors.response.use(
  (r) => r,
  (err) => {
    const status = err?.response?.status;
    const url = err?.config?.url || "";
    const isAuthCall = url.startsWith("/auth/login") || url.startsWith("/auth/register") || url === "/auth/me";
    if (status === 401 && !isAuthCall) window.dispatchEvent(new CustomEvent("gh:unauthorized"));
    if (status === 402) {
      window.dispatchEvent(new CustomEvent("gh:upgrade-required", { detail: errorMessage(err) }));
    }
    return Promise.reject(err);
  }
);

/** Human-readable message from an axios error (FastAPI `detail` or validation list). */
export function errorMessage(err, fallback = "Something went wrong. Please try again.") {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d.length) {
    const first = d[0];
    const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : "";
    if (first.type?.includes("email") || field === "email") return "Please enter a valid email address.";
    return first.msg || fallback;
  }
  if (!err?.response) return "Can't reach the server. Check your connection and try again.";
  return fallback;
}

export const health = () => api.get("/health").then((r) => r.data);

export const listProjects = () => api.get("/projects").then((r) => r.data);
export const createProject = (name, description = "") =>
  api.post("/projects", { name, description }).then((r) => r.data);
export const getProject = (id) => api.get(`/projects/${id}`).then((r) => r.data);
export const deleteProject = (id) => api.delete(`/projects/${id}`).then((r) => r.data);

// mesher "builtin" (structured / Delaunay generators) or "gmsh" (with the Gmsh options object, see GmshOptions.jsx)
export const generatePrimitive = (id, shape, dims, divisions, element_type = "C3D4", mesher = "builtin", gmsh = null) =>
  api.post(`/projects/${id}/mesh/primitive`, { shape, dims, divisions, element_type, mesher, ...(gmsh ? { gmsh } : {}) },
    { timeout: mesher === "gmsh" ? 240000 : undefined }).then((r) => r.data);
export const appendPart = (id, shape, dims, divisions, offset, element_type, name, material_id, mesher = "builtin", gmsh = null) =>
  api.post(`/projects/${id}/mesh/append`, { shape, dims, divisions, offset, element_type, name, material_id, mesher, ...(gmsh ? { gmsh } : {}) },
    { timeout: mesher === "gmsh" ? 240000 : undefined }).then((r) => r.data);
export const generateStructural = (id, body) => api.post(`/projects/${id}/mesh/structural`, body).then((r) => r.data);
export const appendStructural = (id, body) => api.post(`/projects/${id}/mesh/structural/append`, body).then((r) => r.data);
export const customStructural = (id, body) => api.post(`/projects/${id}/mesh/structural/custom`, body).then((r) => r.data);
export const addMaterial = (id, m) => api.post(`/projects/${id}/materials`, m).then((r) => r.data);
export const updateMaterial = (id, mid, m) => api.put(`/projects/${id}/materials/${mid}`, m).then((r) => r.data);
export const deleteMaterial = (id, mid) => api.delete(`/projects/${id}/materials/${mid}`).then((r) => r.data);
export const updatePart = (id, pid, patch) => api.put(`/projects/${id}/parts/${pid}`, patch).then((r) => r.data);
export const translatePart = (id, pid, offset) => api.post(`/projects/${id}/parts/${pid}/translate`, { offset }).then((r) => r.data);
export const undoEdit = (id) => api.post(`/projects/${id}/undo`).then((r) => r.data);
export const redoEdit = (id) => api.post(`/projects/${id}/redo`).then((r) => r.data);
export const gotoHistory = (id, seq) => api.post(`/projects/${id}/history/${seq}`).then((r) => r.data);

// model stack (several models in one project; the project's mesh/setup fields are the active one)
export const addLayer = (id, body) => api.post(`/projects/${id}/layers`, body).then((r) => r.data);
export const updateLayer = (id, lid, patch) => api.put(`/projects/${id}/layers/${lid}`, patch).then((r) => r.data);
export const activateLayer = (id, lid) => api.post(`/projects/${id}/layers/${lid}/activate`).then((r) => r.data);
export const moveLayer = (id, lid, direction) => api.post(`/projects/${id}/layers/${lid}/move`, { direction }).then((r) => r.data);
export const deleteLayer = (id, lid) => api.delete(`/projects/${id}/layers/${lid}`).then((r) => r.data);
export const layerPreview = (id, lid) => api.get(`/projects/${id}/layers/${lid}/preview`).then((r) => r.data);
export const deletePart = (id, pid) => api.delete(`/projects/${id}/parts/${pid}`).then((r) => r.data);
export const importMesh = (id, file) => {
  const form = new FormData();
  form.append("file", file);
  return api.post(`/projects/${id}/mesh/import`, form).then((r) => r.data);
};
export const importCad = (id, file, { size = 0, order = 2, unit = "mm", family = "tet", options = null } = {}) => {
  const form = new FormData();
  form.append("file", file);
  form.append("size", String(size));
  form.append("order", String(order));
  form.append("unit", unit);
  form.append("family", family);
  if (options) form.append("options", JSON.stringify(options));
  return api.post(`/projects/${id}/mesh/cad`, form, { timeout: 240000 }).then((r) => r.data);
};
export const meshQuality = (id) => api.get(`/projects/${id}/mesh/quality`).then((r) => r.data);
export const capabilities = () => api.get("/capabilities").then((r) => r.data);
export const setSettings = (id, settings) => api.put(`/projects/${id}/settings`, settings).then((r) => r.data);
export const addReferencePoint = (id, rp) => api.post(`/projects/${id}/reference-points`, rp).then((r) => r.data);
export const deleteReferencePoint = (id, rid) => api.delete(`/projects/${id}/reference-points/${rid}`).then((r) => r.data);
export const addConstraint = (id, c) => api.post(`/projects/${id}/constraints`, c).then((r) => r.data);
export const deleteConstraint = (id, cid) => api.delete(`/projects/${id}/constraints/${cid}`).then((r) => r.data);
export const addNodeSet = (id, s) => api.post(`/projects/${id}/node-sets`, s).then((r) => r.data);
export const deleteNodeSet = (id, sid) => api.delete(`/projects/${id}/node-sets/${sid}`).then((r) => r.data);
export const importProject = (data) => api.post("/projects/import", data).then((r) => r.data);
export const exportProjectText = (id) => api.get(`/projects/${id}/export/project`, { responseType: "text", transformResponse: (d) => d }).then((r) => r.data);
export const exportInpText = (id) => api.get(`/projects/${id}/export/inp`, { responseType: "text", transformResponse: (d) => d }).then((r) => r.data);

export const setMaterial = (id, material) =>
  api.put(`/projects/${id}/material`, material).then((r) => r.data);

export const setAnalysisType = (id, analysis_type, n_modes = 6) =>
  api.put(`/projects/${id}/analysis-type`, { analysis_type, n_modes }).then((r) => r.data);

export const setGravity = (id, gravity) =>
  api.put(`/projects/${id}/gravity`, gravity).then((r) => r.data);

export const addBC = (id, bc) => api.post(`/projects/${id}/bcs`, bc).then((r) => r.data);
export const deleteBC = (id, bcId) =>
  api.delete(`/projects/${id}/bcs/${bcId}`).then((r) => r.data);

export const addLoad = (id, load) =>
  api.post(`/projects/${id}/loads`, load).then((r) => r.data);
export const deleteLoad = (id, loadId) =>
  api.delete(`/projects/${id}/loads/${loadId}`).then((r) => r.data);

export const solve = (id) => api.post(`/projects/${id}/solve`).then((r) => r.data);
export const getJob = (jobId) => api.get(`/jobs/${jobId}`).then((r) => r.data);
export const latestJob = (id) => api.get(`/projects/${id}/jobs/latest`).then((r) => r.data);
export const listJobs = (id) => api.get(`/projects/${id}/jobs`).then((r) => r.data);
export const listJobSummaries = (id) => api.get(`/projects/${id}/jobs/summary`).then((r) => r.data);

// ------------------------------------------------------------------ AI agent
export const agentStatus = () => api.get("/agent/status").then((r) => r.data);
/** messages: [{role: "user" | "assistant", content}] -> {reply, steps, run_simulation, project} */
export const agentChat = (id, messages) =>
  api.post(`/projects/${id}/agent/chat`, { messages }, { timeout: 240000 }).then((r) => r.data);

// ------------------------------------------------------------------ accounts
export const me = () => api.get("/auth/me").then((r) => r.data);
export const register = (email, password, full_name) =>
  api.post("/auth/register", { email, password, full_name }).then((r) => r.data);
export const login = (email, password) => api.post("/auth/login", { email, password }).then((r) => r.data);
export const logout = () => api.post("/auth/logout");
export const changePassword = (current_password, new_password) =>
  api.post("/auth/change-password", { current_password, new_password });
export const forgotPassword = (email) => api.post("/auth/forgot-password", { email });
export const resetPassword = (token, password) => api.post("/auth/reset-password", { token, password });

// ------------------------------------------------------------------ billing
export const getPlans = () => api.get("/billing/plans").then((r) => r.data);
export const startCheckout = (plan_id) => api.post("/billing/checkout", { plan_id }).then((r) => r.data);
export const getPayments = () => api.get("/billing/payments").then((r) => r.data);
export const getPayment = (id) => api.get(`/billing/payments/${id}`).then((r) => r.data);
export const verifyPayment = (id) => api.post(`/billing/payments/${id}/verify`).then((r) => r.data);
export const mockComplete = (id, outcome) =>
  api.post(`/billing/mock/${id}/complete`, { outcome }).then((r) => r.data);

export default api;

// ---- the project's three workspaces (FEA / CFD / CAD) and the exchange between them (backend bridge.py)
export const projectWorkspaces = (id) => api.get(`/projects/${id}/workspaces`).then((r) => r.data);
export const bridgeSources = (id) => api.get(`/projects/${id}/bridge/sources`).then((r) => r.data);
// meshing / sewing / tessellating can take a while (Gmsh runs with a 180 s limit)
export const bridgeToFea = (id, body) => api.post(`/projects/${id}/bridge/to-fea`, body, { timeout: 240000 }).then((r) => r.data);
export const bridgeToCad = (id, body) => api.post(`/projects/${id}/bridge/to-cad`, body, { timeout: 240000 }).then((r) => r.data);
export const bridgeToCfd = (id, body) => api.post(`/projects/${id}/bridge/to-cfd`, body, { timeout: 240000 }).then((r) => r.data);
export const bridgeCfdToFea = (id, body) => api.post(`/projects/${id}/bridge/cfd-to-fea`, body, { timeout: 120000 }).then((r) => r.data);
/** Open a project file into an existing project (FEA models, CFD cases and CAD documents are added). */
export const mergeProjectFile = (id, data, { fea = true, cfd = true, cad = true } = {}) =>
  api.post(`/projects/${id}/merge-file`, data, { params: { fea, cfd, cad } }).then((r) => r.data);
export const loadCadDemo = (cadProjectId) => api.post(`/cad/projects/${cadProjectId}/demo`).then((r) => r.data);
