import React, { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import workspaceCss from "../styles.css?inline";
import { useAuth } from "../auth/AuthContext.jsx";
import { Logo } from "../brand/Logo.jsx";
import Viewport3D from "../components/Viewport3D.jsx";
import GeometryPanel from "../components/GeometryPanel.jsx";
import MaterialPanel from "../components/MaterialPanel.jsx";
import PartsPanel from "../components/PartsPanel.jsx";
import StructuralPanel from "../components/StructuralPanel.jsx";
import AnalysisPanel from "../components/AnalysisPanel.jsx";
import BCLoadPanel from "../components/BCLoadPanel.jsx";
import ConstraintPanel from "../components/ConstraintPanel.jsx";
import SelectionPanel from "../components/SelectionPanel.jsx";
import JobPanel from "../components/JobPanel.jsx";
import ProjectPanel from "../components/ProjectPanel.jsx";
import MessagesPanel from "../components/MessagesPanel.jsx";
import AgentPanel from "../components/AgentPanel.jsx";
import GuidePanel from "../components/GuidePanel.jsx";
import Legend from "../components/Legend.jsx";
import TreeSection from "../components/TreeSection.jsx";
import Ribbon, { WORKSPACES } from "../components/Ribbon.jsx";
import CommandModal from "../components/CommandModal.jsx";
import SidePanel from "../components/BrowserPanel.jsx";
import GeometryBrowser from "../components/GeometryBrowser.jsx";
import ModelStack from "../components/ModelStack.jsx";
import ResultsBrowser from "../components/ResultsBrowser.jsx";
import CamPanel from "../components/CamPanel.jsx";
import BridgePanel from "../components/BridgePanel.jsx";
import { CAD_TABS } from "../cad/tabs.ts";
import {
  ELEMENTS, autoScale, boundingBox, buildDisplay, buildTubeView, defaultField, download, facesByAngle, fmt, isLineElement,
  isStructural, minMax, resultsMatchMesh, resultsToCSV, resultsToVTK, surfaceNodeIds,
} from "../components/utils.js";
import { buildGlyphs } from "../components/glyphs.js";
import * as api from "../api.js";

// The CFD and CAD workbenches are separate bundle chunks, loaded the first time their workspace is opened.
const CfdPane = lazy(() => import("../workspaces/CfdPane.jsx"));
const CadPane = lazy(() => import("../workspaces/CadPane.jsx"));

function getInitialTheme() {
  try {
    return document.documentElement.getAttribute("data-theme") || "light";
  } catch (e) {
    return "light";
  }
}

/**
 * The simulation workspace. Its stylesheet is global (element selectors such as `button` and `label`),
 * so it is injected only while this route is mounted instead of leaking into the marketing pages.
 */
function useWorkspaceStyles() {
  useLayoutEffect(() => {
    const el = document.createElement("style");
    el.setAttribute("data-workspace", "");
    el.textContent = workspaceCss;
    document.head.appendChild(el);
    return () => el.remove();
  }, []);
}

const navBtn = { textDecoration: "none", whiteSpace: "nowrap", flex: "none", width: "auto", padding: "0 10px", fontSize: 13 };

const DEFAULT_VIEW = {
  deformScale: 1, scaleMax: 10, animate: false, showUndeformed: false, showEdges: false, showMinMax: false,
  rangeAuto: true, rangeMin: 0, rangeMax: 1, probeMode: false,
  clip: { enabled: false, axis: 0, pos: 0.5, flip: false },
};

/** Ribbon commands that open a dialog. The id doubles as the Guide's data-tour id. */
const COMMANDS = {
  project: { label: "Project", icon: "project", title: "Projects: new, open, save, export, undo / redo" },
  geometry: { label: "Geometry", icon: "geometry", title: "Primitives, meshing (built-in or Gmsh), mesh and CAD import" },
  structural: { label: "Frames", icon: "structural", title: "Beams, trusses, shells and springs" },
  parts: { label: "Parts", icon: "parts", title: "Rename, hide, move, delete parts; material per part" },
  materials: { label: "Materials", icon: "materials", title: "Material library and properties" },
  selection: { label: "Select", icon: "selection", title: "Select nodes / faces by click, coordinates, angle or id; named sets" },
  stock: { label: "Stock", icon: "stock", title: "Stock block around the active FEA model and the material to remove (full CAM: CAD workspace)" },
  transfer: { label: "Transfer", icon: "transfer", title: "Send geometry and loads between the FEA, CFD and CAD workspaces of this project" },
};

/**
 * Ribbon layout per workspace (FEA / CFD / CAD). "|" separates groups; strings are COMMANDS ids or actions.
 * FEA keeps every command of the former CAD / CAM / CAE sub-workspaces (geometry, frames, parts, materials, selection,
 * stock setup, solve); CFD's commands open its pages, CAD's switch between its six workspaces.
 */
const RIBBON = {
  fea: ["project", "|", "undo", "redo", "|", "geometry", "structural", "|", "parts", "materials", "selection", "|", "stock", "|",
    "solve", "|", "transfer"],
  cfd: ["project", "|", "cases", "setup", "runs", "results", "|", "transfer"],
  cad: ["project", "|", ...CAD_TABS.map((t) => `cad:${t.id}`), "|", "demo", "transfer"],
};
const CFD_PAGES = {
  cases: { label: "Cases", icon: "cases", title: "The CFD cases of this project: open one, or start from a validated example" },
  setup: { label: "Setup", icon: "setup", title: "1 Setup: general, geometry, mesh, models, materials, boundaries, initialization, run conditions, monitors" },
  runs: { label: "Run", icon: "run", title: "2 Run & monitor: start a run, live residuals, monitors and flow preview" },
  results: { label: "Results", icon: "results", title: "3 Results: contours, vectors, streamlines, line probe, animation, exports" },
};

/** Which workspace a URL shows: /app = FEA, /app/cfd/... = CFD, /app/cad = CAD. */
function workspaceOf(pathname) {
  if (pathname.startsWith("/app/cfd")) return "cfd";
  if (pathname.startsWith("/app/cad")) return "cad";
  return "fea";
}

/** Briefly highlight a workspace section (opening it if it is a collapsed tree section). */
function flash(id) {
  window.dispatchEvent(new CustomEvent("gh:open-command", { detail: id }));   // opens a collapsed column holding it
  setTimeout(() => {
    const el = document.querySelector(`[data-tour="${id}"]`);
    if (!el) return;
    const header = el.querySelector(".tree-section-header");
    if (header && !el.querySelector(".tree-section-body")) header.click();
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    el.classList.add("tour-highlight");
    setTimeout(() => el.classList.remove("tour-highlight"), 1800);
  }, 60);
}

let msgSeq = 0;

export default function Workspace() {
  useWorkspaceStyles();
  const { user, isPro, logout } = useAuth();
  const navigate = useNavigate();
  const [theme, setTheme] = useState(getInitialTheme);
  const [health, setHealth] = useState(null);
  const [caps, setCaps] = useState(null);
  const [projects, setProjects] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [project, setProject] = useState(null);
  const [quality, setQuality] = useState(null);

  const [pickMode, setPickMode] = useState("none");
  const [selectedNodes, setSelectedNodes] = useState([]);
  const [selectedFaces, setSelectedFaces] = useState([]);
  const [angle, setAngle] = useState(30);
  const [hiddenParts, setHiddenParts] = useState(() => new Set());

  const [job, setJob] = useState(null);
  const [solving, setSolving] = useState(false);
  const [sel, setSelState] = useState({ field: "von_mises", frame: 0 });
  const [view, setViewState] = useState(DEFAULT_VIEW);
  const [probeNode, setProbeNode] = useState(null);
  const [viewCommand, setViewCommand] = useState({ name: "iso", n: 0 });
  const [messages, setMessages] = useState([]);
  const [msgOpen, setMsgOpen] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const location = useLocation();
  const workspace = workspaceOf(location.pathname);   // FEA / CFD / CAD: which body and commands the workspace shows
  const workspaceRef = useRef(workspace);
  workspaceRef.current = workspace;
  const lastCfdPath = useRef("/app/cfd");              // reopen the CFD page that was open last
  const [summary, setSummary] = useState(null);        // what the project's FEA / CFD / CAD workspaces hold
  const [cfdCase, setCfdCase] = useState(null);        // the open CFD case (null on the cases list)
  const [cfdKey, setCfdKey] = useState(0);             // bump to reload the CFD workspace (after a Transfer changed the case)
  const [cadTab, setCadTab] = useState("model");       // Sketch / Model / Assembly / Drawing / CAM / Simulate
  const [cadKey, setCadKey] = useState(0);             // bump to reload the CAD workspace
  const [openCmd, setOpenCmd] = useState(null);        // ribbon command whose dialog is open
  const [runs, setRuns] = useState([]);                 // recent runs of the active model (Results tree)
  const [ghosts, setGhosts] = useState([]);             // visible inactive models, drawn for reference
  const [layerBusy, setLayerBusy] = useState(false);
  const closeCmd = useCallback(() => setOpenCmd(null), []);
  const viewApi = useRef(null);
  const pollRef = useRef(null);
  const ghostCache = useRef(new Map());

  const setSel = useCallback((patch) => setSelState((s) => ({ ...s, ...patch })), []);
  const setView = useCallback((patch) => setViewState((v) => ({ ...v, ...patch })), []);
  const setViewName = useCallback((name) => setViewCommand((c) => ({ name, n: c.n + 1 })), []);

  const notify = useCallback((text, level = "info") => {
    setMessages((m) => [...m.slice(-199), { id: ++msgSeq, level, text, time: new Date().toLocaleTimeString() }]);
    if (level === "error" || level === "warn") setMsgOpen(true);
  }, []);

  /** Run an API call; failures become messages instead of unhandled rejections. */
  const guard = useCallback(async (promise, fallback) => {
    try {
      return await promise;
    } catch (err) {
      if (err?.response?.status !== 402) notify(api.errorMessage(err, fallback), "error");
      return undefined;
    }
  }, [notify]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("webfea-theme", theme); } catch (e) { /* storage unavailable */ }
    window.dispatchEvent(new Event("cfdflow-theme"));   // the CFD canvases re-read their colours
  }, [theme]);

  useEffect(() => { if (workspace === "cfd") lastCfdPath.current = location.pathname; }, [workspace, location.pathname]);

  useEffect(() => () => pollRef.current && clearInterval(pollRef.current), []);

  /* ------------------------------------------------------ results display */
  const clearResults = useCallback(() => {
    setJob(null);
    setProbeNode(null);
    setViewState((v) => ({ ...DEFAULT_VIEW, showEdges: v.showEdges, clip: v.clip }));
  }, []);

  const showResults = useCallback((j, p) => {
    // a run made before the mesh was changed would be drawn on the wrong nodes
    if (j?.status === "completed" && !resultsMatchMesh(j.results, p.nodes.length)) { setJob(null); return false; }
    setJob(j);
    if (j?.status !== "completed") return true;
    const r = j.results;
    const field = defaultField(r);
    // open dynamics at the peak response and transient heat at the final state
    const frame = r.analysis_type === "dynamic" ? r.peak_frame || 0 : r.analysis_type === "thermal_transient" ? (r.frames?.length || 1) - 1 : 0;
    setSelState({ field, frame });
    const d = buildDisplay(r, { field, frame }, p.nodes.length);
    const scale = autoScale(d.disp, boundingBox(p.nodes).size);
    setViewState((v) => ({
      ...v, deformScale: scale, scaleMax: Math.max(scale * 10, 10), rangeAuto: true, probeMode: false,
      animate: r.analysis_type === "modal" || r.analysis_type === "buckling",
    }));
    setProbeNode(null);
    return true;
  }, []);

  const results = job?.status === "completed" ? job.results : null;
  const nNodes = project?.nodes?.length || 0;
  const display = useMemo(() => buildDisplay(results, sel, nNodes), [results, sel, nNodes]);
  const fieldRange = useMemo(() => (display.field && display.field.length ? minMax(display.field) : [0, 1]), [display.field]);
  const range = view.rangeAuto ? null : [view.rangeMin, view.rangeMax];
  const legendRange = range || fieldRange;

  const probe = useMemo(() => {
    if (probeNode === null || !results) return null;
    const rows = [];
    const d = display.disp?.[probeNode];
    if (d) rows.push(["u (x,y,z)", d.map((v) => fmt(v, 3)).join(", ")]);
    if (display.field) rows.push([(display.label || "value").split("·")[0].trim(), `${fmt(display.field[probeNode], 5)} ${display.unit || ""}`]);
    Object.entries(results.fields || {}).forEach(([k, arr]) => {
      if (["von_mises", "temperature", "plastic_strain", "tresca"].includes(k)) rows.push([k.replace(/_/g, " "), fmt(arr[probeNode], 5)]);
    });
    return { node: probeNode, rows };
  }, [probeNode, results, display]);

  /* ---------------------------------------------------------- project load */
  const loadQuality = useCallback((id) => {
    api.meshQuality(id).then(setQuality).catch(() => setQuality(null));
  }, []);

  const selectProject = useCallback(async (id) => {
    setActiveId(id);
    setHiddenParts(new Set());
    setSelectedNodes([]);
    setSelectedFaces([]);
    setQuality(null);
    clearResults();
    const p = await guard(api.getProject(id));
    if (!p) return;
    setProject(p);
    if (p.elements?.length) {
      loadQuality(id);
      api.latestJob(id).then((j) => showResults(j, p)).catch(() => {});
    }
  }, [guard, clearResults, loadQuality, showResults]);

  /* ------------------------------------------------------------ model stack */
  /** Another model became the active one: adopt it as if the project had just been opened. */
  const adoptModel = useCallback((p) => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setSolving(false);
    setProject(p);
    clearResults();
    setSelectedNodes([]);
    setSelectedFaces([]);
    setHiddenParts(new Set());
    setQuality(null);
    setPickMode("none");
    if (p.elements?.length) {
      loadQuality(p.id);
      api.latestJob(p.id).then((j) => showResults(j, p)).catch(() => {});
    }
    setViewName("iso");
  }, [clearResults, loadQuality, showResults, setViewName]);

  const layerCall = async (promise, adopt) => {
    setLayerBusy(true);
    try {
      const p = await guard(promise);
      if (p) (adopt ? adoptModel(p) : setProject(p));
      return p;
    } finally {
      setLayerBusy(false);
    }
  };
  const activeLayer = project?.layer_stack?.find((l) => l.active);
  const handleActivateLayer = async (lid) => {
    if (solving) notify("The running analysis continues; its results appear when you return to that model.", "warn");
    const p = await layerCall(api.activateLayer(activeId, lid), true);
    if (p) notify(`Active model: “${p.layer_stack.find((l) => l.active)?.name}”.`);
  };
  const handleAddLayer = async () => {
    const n = (project?.layer_stack?.length || 0) + 1;
    const p = await layerCall(api.addLayer(activeId, { name: `Model ${n}`, process: "cae" }), true);
    if (p) notify(`Added “Model ${n}” on top of the stack. Create or import its geometry.`);
  };
  const handleDuplicateLayer = async (layer) => {
    const p = await layerCall(api.addLayer(activeId, { name: `${layer.name} copy`.slice(0, 80), process: layer.process, duplicate_of: layer.id }), true);
    if (p) notify(`Duplicated “${layer.name}”. The copy is now the active model.`);
  };
  const handleDeleteLayer = async (layer) => {
    if (!window.confirm(`Delete the model “${layer.name}” with its setup, history and results?`)) return;
    const p = await layerCall(api.deleteLayer(activeId, layer.id), true);
    if (p) notify(`Deleted “${layer.name}”.`);
  };
  const handleRenameLayer = (lid, name) => layerCall(api.updateLayer(activeId, lid, { name }), false);
  const handleToggleLayer = (lid, visible) => layerCall(api.updateLayer(activeId, lid, { visible }), false);
  const handleMoveLayer = (lid, dir) => layerCall(api.moveLayer(activeId, lid, dir), false);
  const handleWorkspace = (w) => {
    if (w === workspace) return;
    setOpenCmd(null);
    if (w !== "fea") { setPickMode("none"); setAgentOpen(false); setGuideOpen(false); }
    navigate(w === "cfd" ? lastCfdPath.current : w === "cad" ? "/app/cad" : "/app");
  };

  /* --------------------------------------------- the project's CFD / CAD workspaces */
  const refreshSummary = useCallback(async (id) => {
    if (!id) { setSummary(null); return; }
    const s = await api.projectWorkspaces(id).catch(() => null);
    setSummary((cur) => (s && s.project_id === id ? s : cur && cur.project_id === id ? cur : null));
  }, []);
  useEffect(() => { refreshSummary(activeId); }, [activeId, refreshSummary]);
  const cadProjectId = summary?.project_id === activeId ? summary?.cad?.cad_project_id : null;
  const onCfdCase = useCallback((c) => setCfdCase(c), []);
  const cfdPage = location.pathname.match(/^\/app\/cfd\/[^/]+\/(setup|runs|results)/)?.[1] || "cases";
  const cfdCaseInUrl = location.pathname.match(/^\/app\/cfd\/([^/]+)/)?.[1] || null;
  const goCfd = (page) => {
    if (page === "cases") navigate("/app/cfd");
    else if (cfdCaseInUrl) navigate(`/app/cfd/${cfdCaseInUrl}/${page}`);
  };
  const handleLoadDemo = async () => {
    if (!cadProjectId) return;
    if (!(await guard(api.loadCadDemo(cadProjectId)))) return;
    setCadKey((k) => k + 1);
    refreshSummary(activeId);
    notify("Added the demo to the CAD workspace: parametric mounting plate, pins, an assembly with mates and a CAM setup.");
  };
  /* Transfer results: adopt what the server changed in the workspace it went to */
  const handleBridgeFea = (p) => {
    adoptModel(p);
    refreshSummary(p.id);
    notify(workspace === "fea" ? "The FEA model was updated by the transfer." : "The FEA model was updated - switch to FEA to see it.");
  };
  const handleBridgeCfd = (caseId) => {
    setCfdKey((k) => k + 1);
    refreshSummary(activeId);
    if (workspace === "cfd") navigate(`/app/cfd/${caseId}/setup`);
    else { lastCfdPath.current = `/app/cfd/${caseId}/setup`; notify("The CFD case was updated - switch to CFD to see it."); }
  };
  const handleBridgeCad = () => {
    setCadKey((k) => k + 1);
    refreshSummary(activeId);
    if (workspace !== "cad") notify("New CAD parts were created - switch to CAD to see them.");
  };

  // reference ghosts of the visible inactive models (previews are cached until that model changes)
  useEffect(() => {
    const wanted = (project?.layer_stack || []).filter((l) => !l.active && l.visible && l.summary?.n_elements);
    if (!wanted.length) { setGhosts([]); return undefined; }
    let alive = true;
    Promise.all(wanted.map(async (l) => {
      const key = `${project.id}:${l.id}:${l.updated_at}`;
      if (!ghostCache.current.has(key)) ghostCache.current.set(key, await api.layerPreview(project.id, l.id));
      return { ...ghostCache.current.get(key), id: l.id };
    })).then((g) => alive && setGhosts(g)).catch(() => alive && setGhosts([]));
    return () => { alive = false; };
  }, [project?.id, project?.layer_stack]);

  // runs of the active model for the Results tree
  const activeLayerId = project?.active_layer_id;
  useEffect(() => {
    if (!activeId) { setRuns([]); return; }
    api.listJobSummaries(activeId).then(setRuns).catch(() => setRuns([]));
  }, [activeId, activeLayerId, job?.status]);
  const handleLoadRun = async (run) => {
    const j = await guard(api.getJob(run.id));
    if (j && !showResults(j, project)) notify("That run was computed on an earlier version of the mesh, so its results cannot be shown on the current one.", "warn");
  };

  const refreshProjects = useCallback(async () => {
    const ps = await guard(api.listProjects());
    if (ps) setProjects(ps);
    return ps || [];
  }, [guard]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: "unreachable" }));
    api.capabilities().then(setCaps).catch(() => setCaps(null));
    (async () => {
      const ps = await refreshProjects();
      // a link straight to a CFD case (/app/cfd/<case>/...) opens the project that case belongs to
      const caseId = window.location.pathname.match(/^\/app\/cfd\/([0-9a-f]{8,})/)?.[1];
      const owner = caseId ? await fetch(`/api/cfd/projects/${caseId}`, { credentials: "include" })
        .then((r) => (r.ok ? r.json() : null)).then((c) => c?.project_id).catch(() => null) : null;
      if (ps.length) selectProject(ps.some((p) => p.id === owner) ? owner : ps[0].id);
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ------------------------------------------------------- project actions */
  /** Apply a model edit: replace the project and drop now-stale results. */
  const mutate = useCallback(async (promise, { reload = false } = {}) => {
    const p = await guard(promise);
    if (!p) return undefined;
    setProject(p);
    clearResults();
    if (reload) loadQuality(p.id);
    return p;
  }, [guard, clearResults, loadQuality]);

  const clearSel = () => { setSelectedNodes([]); setSelectedFaces([]); };

  const handleCreate = async (name) => {
    const p = await guard(api.createProject(name));
    if (!p) return;
    await refreshProjects();
    selectProject(p.id);
    notify(`Created project “${p.name}”.`);
  };

  const handleDelete = async (id) => {
    if (!(await guard(api.deleteProject(id)))) return;
    if (workspace === "cfd") navigate("/app/cfd");
    if (id === activeId) { setActiveId(null); setProject(null); clearResults(); }
    const ps = await refreshProjects();
    if (id === activeId && ps.length) selectProject(ps[0].id);
  };

  const handleSave = async () => {
    const text = await guard(api.exportProjectText(activeId));
    if (text) {
      download(`${project.name.replace(/[^\w-]+/g, "_")}.gearhead.json`, text, "application/json");
      notify("Project saved: FEA models, CFD cases and CAD documents in one .gearhead.json file.");
    }
  };
  const handleOpen = async (file) => {
    let data;
    try {
      data = JSON.parse(await file.text());
    } catch (e) {
      notify("That file is not valid JSON.", "error");
      return;
    }
    const p = await guard(api.importProject(data), "That file is not a valid project.");
    if (!p) return;
    await refreshProjects();
    if (workspace === "cfd") navigate("/app/cfd");
    selectProject(p.id);
    notify(`Opened “${p.name}” as a new project.`);
  };
  const handleMerge = async (file) => {
    let data;
    try {
      data = JSON.parse(await file.text());
    } catch (e) {
      notify("That file is not valid JSON.", "error");
      return;
    }
    const r = await guard(api.mergeProjectFile(activeId, data), "That file is not a valid project.");
    if (!r) return;
    const a = r.added;
    selectProject(activeId);
    setCfdKey((k) => k + 1);
    setCadKey((k) => k + 1);
    setSummary(r.workspaces);
    notify(`Opened ${file.name} into this project: ${a.fea_models} FEA model(s), ${a.cfd_cases} CFD case(s), `
      + `${a.cad_parts} CAD part(s), ${a.cad_sketches} sketch(es), ${a.cad_assemblies} assembl(ies), ${a.cad_drawings} drawing(s).`);
  };
  const handleExportInp = async () => {
    const text = await guard(api.exportInpText(activeId));
    if (text) { download(`${project.name.replace(/[^\w-]+/g, "_")}.inp`, text); notify("Exported a CalculiX/Abaqus-style .inp deck."); }
  };

  const handleGenerate = async (shape, dims, divisions, etype, mesher = "builtin", gmshOpts = null) => {
    if (mesher === "gmsh") notify("Meshing with Gmsh…");
    const p = await mutate(api.generatePrimitive(activeId, shape, dims, divisions, etype, mesher, gmshOpts), { reload: true });
    if (p) { clearSel(); setViewName("iso"); notify(`Meshed: ${p.nodes.length} nodes, ${p.elements.length} ${p.element_type} elements.`); }
  };
  const handleAppend = async (shape, dims, divisions, offset, name, materialId, mesher = "builtin", gmshOpts = null) => {
    const p = await mutate(
      api.appendPart(activeId, shape, dims, divisions, offset, project.element_type, name, materialId, mesher, gmshOpts),
      { reload: true });
    if (p) { clearSel(); notify(`Added part “${p.parts[p.parts.length - 1].name}”. The mesh now has ${p.nodes.length} nodes.`); }
  };

  const handleImportCad = async (file, opts) => {
    notify(`Meshing ${file.name}… this can take up to a minute.`);
    const p = await mutate(api.importCad(activeId, file, opts), { reload: true });
    if (p) { clearSel(); setViewName("iso"); notify(`Imported ${file.name}: ${p.parts.length} part(s), ${p.nodes.length} nodes, ${p.elements.length} ${p.element_type} elements.`); }
  };
  const handleGenerateStructural = async (body) => {
    const p = await mutate(api.generateStructural(activeId, body), { reload: true });
    if (p) { clearSel(); setViewName("iso"); notify(`Created ${p.nodes.length} nodes, ${p.elements.length} ${p.element_type} elements.`); }
  };
  const handleAppendStructural = async (body) => {
    const p = await mutate(api.appendStructural(activeId, body), { reload: true });
    if (p) { clearSel(); notify(`Added part “${p.parts[p.parts.length - 1].name}” (${p.elements.length} elements in total).`); }
  };
  const handleCustomStructural = async (body) => {
    const p = await mutate(api.customStructural(activeId, body), { reload: true });
    if (p) { clearSel(); setViewName("iso"); notify(`Created ${p.nodes.length} nodes, ${p.elements.length} ${p.element_type} elements.`); }
  };

  /* ------------------------------------------------------ materials & parts */
  const handleMaterialUpdate = (id, m) => mutate(id === "default" ? api.setMaterial(activeId, m) : api.updateMaterial(activeId, id, m));
  const handleMaterialAdd = (m) => mutate(api.addMaterial(activeId, m));
  const handleMaterialDelete = (id) => mutate(api.deleteMaterial(activeId, id));
  const handlePartUpdate = (pid, patch) => mutate(api.updatePart(activeId, pid, patch));
  const handlePartMove = async (pid, offset) => {
    const p = await mutate(api.translatePart(activeId, pid, offset));
    if (p) notify("Part moved. Re-check ties and supports that depend on its position.");
  };
  const handlePartDelete = async (pid) => {
    const p = await mutate(api.deletePart(activeId, pid), { reload: true });
    if (p) { clearSel(); notify("Part deleted. Supports, loads and sets that only used it were removed."); }
  };
  const togglePart = (pid) => setHiddenParts((h) => {
    const n = new Set(h);
    if (n.has(pid)) n.delete(pid); else n.add(pid);
    return n;
  });
  const handleImportMesh = async (file) => {
    const p = await mutate(api.importMesh(activeId, file), { reload: true });
    if (p) { clearSel(); setViewName("iso"); notify(`Imported ${file.name}: ${p.nodes.length} nodes, ${p.elements.length} ${p.element_type} elements.`); }
  };

  /* --------------------------------------------------------------- undo / redo */
  const labels = project?.history_labels || [];
  const pos = project?.history_pos ?? -1;
  const history = {
    canUndo: pos > 0, canRedo: pos >= 0 && pos < labels.length - 1,
    undoLabel: pos > 0 ? labels[pos] : null, redoLabel: pos >= 0 && pos < labels.length - 1 ? labels[pos + 1] : null,
  };
  const handleUndo = async () => {
    if (!history.canUndo) return;
    const label = history.undoLabel;
    const p = await mutate(api.undoEdit(activeId), { reload: true });
    if (p) { clearSel(); notify(`Undid: ${label}`); }
  };
  const handleRedo = async () => {
    if (!history.canRedo) return;
    const label = history.redoLabel;
    const p = await mutate(api.redoEdit(activeId), { reload: true });
    if (p) { clearSel(); notify(`Redid: ${label}`); }
  };
  const handleGoto = async (seq) => {
    const p = await mutate(api.gotoHistory(activeId, seq), { reload: true });
    if (p) { clearSel(); notify(`Back to: ${labels[seq] || "state " + seq}`); }
  };
  const undoRef = useRef({});
  undoRef.current = { handleUndo, handleRedo };

  // the Guide opens a command's dialog when it spotlights a ribbon command
  useEffect(() => {
    const onOpen = (e) => { if (COMMANDS[e.detail]) setOpenCmd(e.detail); };
    window.addEventListener("gh:open-command", onOpen);
    return () => window.removeEventListener("gh:open-command", onOpen);
  }, []);
  useEffect(() => {
    const onKey = (e) => {
      // FEA undo / redo shortcuts; the CFD / CAD bodies live in shadow roots, so the real target is composedPath()[0]
      if (!(e.ctrlKey || e.metaKey) || e.altKey || workspaceRef.current !== "fea") return;
      const t = e.composedPath?.()[0] || e.target;
      if (t && (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable)) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); undoRef.current.handleUndo(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); undoRef.current.handleRedo(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /* --------------------------------------------------------------- picking */
  /** Node tool: one click selects the one node the cursor is snapped to; shift adds it, or removes it if it is already selected. */
  const handlePickNode = (viewId, additive) => {
    const id = tube ? tube.baseOf[viewId] : viewId;
    setSelectedFaces([]);
    setSelectedNodes((prev) => {
      if (additive) return prev.includes(id) ? prev.filter((n) => n !== id) : [...prev, id];
      return prev.length === 1 && prev[0] === id ? [] : [id];
    });
  };

  /** Face tools. `siblingTri` is the second triangle of the same quadrilateral face on brick meshes: both are selected together. */
  const handlePickFace = (rawTri, additive, faceIdx, siblingTri = null) => {
    const sort = (t) => [...t].sort((a, b) => a - b).join(",");
    const pick = [rawTri, ...(siblingTri ? [siblingTri] : [])];
    if (pickMode === "select-faces") {
      setSelectedNodes([]);
      setSelectedFaces((prev) => {
        const keys = new Set(pick.map(sort));
        const exists = prev.some((f) => keys.has(sort(f)));
        if (exists) return prev.filter((f) => !keys.has(sort(f)));
        return additive ? [...prev, ...pick] : pick;
      });
    } else if (pickMode === "select-angle" && project) {
      setSelectedNodes([]);
      const found = facesByAngle(project.surface_faces, project.nodes, visIdx[faceIdx] ?? faceIdx, angle);
      setSelectedFaces(additive ? [...selectedFaces, ...found] : found);
    }
  };

  /**
   * Box selection from the viewport. `picked.nodes` are view node ids, `picked.faces` indices into the visible faces;
   * op is "replace" (plain drag), "add" (Shift) or "remove" (Ctrl).
   */
  const handleBoxSelect = (picked, op, kind) => {
    const sortKey = (t) => [...t].sort((a, b) => a - b).join(",");
    if (kind === "nodes") {
      const ids = [...new Set(tube ? picked.nodes.map((v) => tube.baseOf[v]) : picked.nodes)];
      setSelectedFaces([]);
      setSelectedNodes((prev) => {
        if (op === "replace") return ids;
        if (op === "add") return [...new Set([...prev, ...ids])];
        const drop = new Set(ids);
        return prev.filter((n) => !drop.has(n));
      });
      if (op !== "remove") notify(`Box selection: ${ids.length} node(s)${op === "add" ? " added" : ""}.`);
      return;
    }
    // brick / S4 faces are quads drawn as two triangles: keep both halves together
    const idx = new Set(picked.faces);
    if (faceSiblings) picked.faces.forEach((i) => { if (faceSiblings[i] >= 0) idx.add(faceSiblings[i]); });
    const tris = [...idx].map((i) => visibleFaces[i]);
    setSelectedNodes([]);
    setSelectedFaces((prev) => {
      if (op === "replace") return tris;
      const keys = new Set(tris.map(sortKey));
      if (op === "remove") return prev.filter((f) => !keys.has(sortKey(f)));
      const have = new Set(prev.map(sortKey));
      return [...prev, ...tris.filter((t) => !have.has(sortKey(t)))];
    });
    if (op !== "remove") notify(`Box selection: ${quadFaces ? Math.ceil(tris.length / 2) : tris.length} face(s)${op === "add" ? " added" : ""}.`);
  };

  /* ------------------------------------------------------------ model edits */
  const afterEdit = (msg) => (p) => { if (p) { clearSel(); if (msg) notify(msg); } };
  const handleAddBC = async (bc) => afterEdit("Added.")(await mutate(api.addBC(activeId, bc)));
  const handleAddLoad = async (ld) => afterEdit("Added.")(await mutate(api.addLoad(activeId, ld)));
  const handleSettings = (patch) => mutate(api.setSettings(activeId, patch));
  const handleType = async (type) => {
    const p = await mutate(api.setAnalysisType(activeId, type, project.settings?.n_modes || project.n_modes || 6));
    if (p) { clearSel(); setPickMode("none"); }
  };

  /* ----------------------------------------------------------------- solve */
  const startPolling = (jobId, p) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const j = await api.getJob(jobId).catch(() => null);
      if (!j) return;
      setJob(j);
      if (j.status === "completed" || j.status === "failed") {
        clearInterval(pollRef.current);
        setSolving(false);
        if (j.status === "failed") notify(j.log, "error");
        else { notify(j.log || "Analysis completed."); showResults(j, p); }
      }
    }, 700);
  };

  /** Start the analysis of `proj` (the manual Solve button and the AI agent both come through here). */
  const runSolve = async (proj) => {
    setSolving(true);
    clearResults();
    const j = await guard(api.solve(proj.id));
    if (!j) { setSolving(false); return; }
    setJob(j);
    notify("Analysis started.");
    startPolling(j.id, proj);
  };
  const handleSolve = () => runSolve(project);

  /** The AI agent edited the project on the server: adopt its version and drop stale results/selection. */
  const handleAgentProject = (p) => {
    setProject(p);
    clearResults();
    clearSel();
    setHiddenParts(new Set());
    if (p.elements?.length) loadQuality(p.id);
    setViewName("iso");
    notify("The AI assistant updated the model.");
  };
  const handleAgentRun = (p) => {
    notify("The AI assistant started the simulation.");
    runSolve(p);
  };

  /* ---------------------------------------------------------------- exports */
  const handleExport = (kind) => {
    if (!results) return;
    const base = (project.name || "results").replace(/[^\w-]+/g, "_");
    if (kind === "csv") download(`${base}_results.csv`, resultsToCSV(project, results, display.disp), "text/csv");
    else if (kind === "vtk") download(`${base}_results.vtk`, resultsToVTK(project, results, display.disp));
    else if (kind === "png" && viewApi.current) {
      const a = document.createElement("a");
      a.href = viewApi.current.capture();
      a.download = `${base}.png`;
      a.click();
    }
  };

  /* ------------------------------------------------------- viewport helpers */
  const structuralType = isStructural(project?.element_type) ? project.element_type : null;
  const lineModel = isLineElement(project?.element_type);
  const tube = useMemo(() => {
    if (!lineModel || !project?.elements?.length) return null;
    const partOf = new Int32Array(project.elements.length);
    (project.parts || []).forEach((p, k) => partOf.fill(k, p.elem_start, p.elem_end));
    const diag = Math.max(Math.hypot(...boundingBox(project.nodes).size), 1e-6);
    const thin = project.element_type === "SPRING2" ? 0.5 : 1;
    return buildTubeView(project.nodes, project.elements, partOf, 0.012 * diag * thin);
  }, [lineModel, project?.nodes, project?.elements, project?.parts, project?.element_type]);
  const viewNodes = tube ? tube.nodes : project?.nodes;
  const toView = useCallback((arr) => {
    if (!tube || !arr) return arr;
    const out = new Array(tube.baseOf.length);
    for (let v = 0; v < out.length; v++) out[v] = arr[tube.baseOf[v]];
    return out;
  }, [tube]);
  const viewDisp = useMemo(() => toView(display.disp), [toView, display.disp]);
  const viewField = useMemo(() => toView(display.field), [toView, display.field]);
  const selectionFaces = useMemo(
    () => (lineModel ? (project?.elements || []).map(([a, b]) => [a, b, b]) : project?.surface_faces || []),
    [lineModel, project?.elements, project?.surface_faces]);

  // Brick (and S4 shell) faces are quadrilaterals drawn as two triangles: triangle i and i + N/2 belong to the same quad.
  const quadFaces = !tube && (ELEMENTS[project?.element_type]?.corners === 8 || project?.element_type === "S4");
  const { visibleFaces, visIdx, faceParts, visibleMid, faceSiblings } = useMemo(() => {
    const faces = tube ? tube.faces : project?.surface_faces || [];
    const owner = tube ? tube.faceParts : project?.surface_parts || [];
    const mid = !tube && project?.surface_mid?.length === faces.length ? project.surface_mid : null;
    const plist = project?.parts || [];
    let vf = faces, vi = faces.map((_, i) => i), fp = owner;
    if (hiddenParts.size) {
      vf = []; vi = []; fp = [];
      faces.forEach((t, i) => {
        const pid = plist[owner[i] ?? 0]?.id;
        if (!hiddenParts.has(pid)) { vf.push(t); vi.push(i); fp.push(owner[i] ?? 0); }
      });
    }
    let siblings = null;
    if (quadFaces && faces.length % 2 === 0) {
      const half = faces.length / 2;
      const where = new Int32Array(faces.length).fill(-1);
      vi.forEach((g, k) => { where[g] = k; });
      siblings = new Int32Array(vi.length);
      vi.forEach((g, k) => { siblings[k] = where[g < half ? g + half : g - half]; });
    }
    return {
      visibleFaces: vf, visIdx: vi, faceParts: fp, faceSiblings: siblings,
      visibleMid: mid ? (hiddenParts.size ? vi.map((g) => mid[g]) : mid) : null,
    };
  }, [tube, quadFaces, project?.surface_faces, project?.surface_parts, project?.surface_mid, project?.parts, hiddenParts]);
  const surfaceSet = useMemo(
    () => new Set(lineModel ? (project?.elements || []).flat() : surfaceNodeIds(visibleFaces)), [lineModel, project?.elements, visibleFaces]);
  const glyphs = useMemo(() => (project ? buildGlyphs(project, surfaceSet) : []), [project, surfaceSet]);

  const analysisType = project?.analysis_type || "static";
  const oscillate = results?.analysis_type === "modal" || results?.analysis_type === "buckling";
  const statusText = !health ? "Connecting…" : health.status !== "ok" ? "Backend unreachable" : "Ready";
  const loadsCount = (project?.boundary_conditions?.length || 0) + (project?.loads?.length || 0);
  const hasMesh = !!project?.elements?.length;

  const ribbonGroups = RIBBON[workspace].map((id) => {
    if (id === "|") return { sep: true };
    if (id === "solve") {
      return { id, label: solving ? "Solving…" : "Solve", icon: "solve", tour: "solve-cmd", action: handleSolve,
        disabled: solving || !hasMesh, title: "Run the analysis of the active model" };
    }
    if (id === "undo") {
      return { id, label: "Undo", icon: "undo", action: handleUndo, disabled: !history.canUndo, plain: true,
        title: history.canUndo ? `Undo: ${history.undoLabel} (Ctrl+Z)` : "Nothing to undo" };
    }
    if (id === "redo") {
      return { id, label: "Redo", icon: "redo", action: handleRedo, disabled: !history.canRedo, plain: true,
        title: history.canRedo ? `Redo: ${history.redoLabel} (Ctrl+Y)` : "Nothing to redo" };
    }
    if (CFD_PAGES[id]) {
      const pg = CFD_PAGES[id];
      return { id, label: pg.label, icon: pg.icon, title: pg.title, action: () => goCfd(id), current: cfdPage === id,
        disabled: !project || (id !== "cases" && !cfdCaseInUrl), plain: false };
    }
    if (id.startsWith("cad:")) {
      const t = CAD_TABS.find((x) => `cad:${x.id}` === id);
      return { id, label: t.label, icon: t.icon, title: `${t.label}: ${t.hint}`, action: () => setCadTab(t.id),
        current: cadTab === t.id, disabled: !cadProjectId };
    }
    if (id === "demo") {
      return { id, label: "Demo", icon: "demo", action: handleLoadDemo, disabled: !cadProjectId, plain: true,
        title: "Add the ready-made demo to this project's CAD workspace: parametric plate, pins, assembly with mates, CAM setup" };
    }
    const c = COMMANDS[id];
    const badge = id === "parts" ? ((project?.parts?.length || 0) > 1 ? project.parts.length : null)
      : id === "selection" ? (selectedNodes.length + selectedFaces.length || null) : null;
    return { id, label: c.label, icon: c.icon, title: c.title, tour: id, badge,
      disabled: (!project && id !== "project") || ((id === "parts" || id === "selection") && !hasMesh) };
  });
  const toggleCmd = (id) => setOpenCmd((o) => (o === id ? null : id));
  const ws = WORKSPACES.find((w) => w.id === workspace);

  return (
    <div className="app">
      <div className="toolbar">
        <div className="brand">
          <Logo tag={ws?.label || "FEA"} />
          <span className="brand-sheet">Workspace</span>
        </div>
        <div className="divider" />
        {project && (
          <span className="appbar-doc" title={workspace === "fea" ? "Project · active FEA model" : workspace === "cfd" ? "Project · CFD case" : "Project · CAD"}>
            <strong>{project.name}</strong>
            {workspace === "fea" && activeLayer && <> · {activeLayer.name}</>}
            {workspace === "cfd" && <> · {cfdCase && cfdCaseInUrl ? cfdCase.name : "CFD cases"}</>}
            {workspace === "cad" && <> · CAD {CAD_TABS.find((t) => t.id === cadTab)?.label}</>}
          </span>
        )}
        {health && <span className={`badge ${health.status === "ok" ? "ok" : "warn"}`}>{statusText}</span>}
        <div className="spacer" />
        {workspace === "fea" && (
          <>
            <button className={`toolbar-btn ${agentOpen ? "active" : ""}`} style={navBtn} onClick={() => setAgentOpen((o) => !o)} data-tour="ai"
              title="Describe a problem in plain language and let the AI build and run the FEA simulation">✦ AI Assistant</button>
            <button className={`toolbar-btn ${guideOpen ? "active" : ""}`} style={navBtn} onClick={() => setGuideOpen((o) => !o)} data-tour="guide"
              title="Step-by-step walkthrough of how to use the FEA workspace">? Guide</button>
          </>
        )}
        <Link className="toolbar-btn" to="/account" title="Account & billing" style={{ ...navBtn, maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis" }}>
          {isPro ? "Pro" : "Free"} · {user?.email}
        </Link>
        {!isPro && <Link className="toolbar-btn active" to="/pricing" style={navBtn}>Upgrade</Link>}
        <button className="toolbar-btn" style={navBtn} onClick={() => logout().finally(() => navigate("/"))}>Sign out</button>
        <button className="toolbar-btn" title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}>
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </div>

      <Ribbon workspace={workspace} onWorkspace={handleWorkspace} groups={ribbonGroups} openId={openCmd} onOpen={toggleCmd} />

      <div className="body" style={workspace === "fea" ? undefined : { display: "none" }}>
        <SidePanel tabs={[
          { id: "geometry", label: "Geometry", icon: "geometry", tours: ["geometry-panel"], content: (
          <GeometryBrowser project={project} quality={quality} hidden={hiddenParts} onToggle={togglePart} onOpen={setOpenCmd} />
          ) },
          { id: "model", label: "Model", icon: "layers", tours: ["models"], content: (
          <ModelStack project={project} busy={layerBusy} onActivate={handleActivateLayer} onAdd={handleAddLayer}
            onDuplicate={handleDuplicateLayer} onRename={handleRenameLayer} onToggle={handleToggleLayer} onMove={handleMoveLayer}
            onDelete={handleDeleteLayer} onOpen={setOpenCmd} onSpotlight={flash} onGoto={handleGoto} />
          ) },
          { id: "analysis", label: "Analysis", icon: "analysis", tours: ["analysis", "loads", "constraints", "solve", "results"], content: (
          <div className="tree-root ptree-forms">
            {project && (
              <TreeSection icon="analysis" title="Analysis type" tour="analysis" badge={analysisType} defaultOpen>
                <AnalysisPanel analysisType={analysisType} settings={project.settings} gravity={project.gravity} isPro={isPro}
                  onChangeType={handleType} onSettings={handleSettings} onGravity={(g) => mutate(api.setGravity(activeId, g))}
                  structural={!!structuralType} />
              </TreeSection>
            )}
            {hasMesh && (
              <TreeSection icon="loads" title="Supports & loads" tour="loads" badge={loadsCount || null} defaultOpen>
                <BCLoadPanel analysisType={analysisType} selectedNodes={selectedNodes} selectedFaces={selectedFaces}
                  boundaryConditions={project.boundary_conditions} loads={project.loads} refPoints={project.reference_points}
                  structuralType={structuralType} parts={project.parts} onAddBC={handleAddBC} onDeleteBC={(id) => mutate(api.deleteBC(activeId, id))}
                  onAddLoad={handleAddLoad} onDeleteLoad={(id) => mutate(api.deleteLoad(activeId, id))} onMessage={notify} />
              </TreeSection>
            )}
            {hasMesh && !structuralType && (
              <TreeSection icon="constraints" title="Constraints" tour="constraints" badge={(project.constraints?.length || 0) + (project.reference_points?.length || 0) || null} defaultOpen={false}>
                <ConstraintPanel nodes={project.nodes} selectedNodes={selectedNodes} selectedFaces={selectedFaces} parts={project.parts}
                  refPoints={project.reference_points} constraints={project.constraints} sets={project.node_sets}
                  onAddRef={(rp) => mutate(api.addReferencePoint(activeId, rp))} onDeleteRef={(id) => mutate(api.deleteReferencePoint(activeId, id))}
                  onAddConstraint={async (c) => afterEdit("Constraint added.")(await mutate(api.addConstraint(activeId, c)))}
                  onDeleteConstraint={(id) => mutate(api.deleteConstraint(activeId, id))} onMessage={notify} />
              </TreeSection>
            )}
            {project && (
              <TreeSection icon="solve" title="Solve & display" tour="solve" defaultOpen>
                <JobPanel onSolve={handleSolve} solving={solving} job={job} project={project} sel={sel} setSel={setSel}
                  view={view} setView={setView} display={display} fieldRange={fieldRange} probe={probe}
                  onExport={handleExport} onClearResults={clearResults}
                  onAutoScale={() => setView({ deformScale: autoScale(display.disp, boundingBox(project.nodes).size) })} />
              </TreeSection>
            )}
            <div data-tour="results">
              <ResultsBrowser project={project} job={job} jobs={runs} solving={solving} sel={sel} setSel={setSel} showRun={false}
                onSolve={handleSolve} onLoadJob={handleLoadRun} onExport={handleExport} onSpotlight={flash} />
            </div>
          </div>
          ) },
        ]} />

        <main className="viewport" data-dialog-anchor="">
          {hasMesh ? (
            <Viewport3D
              nodes={viewNodes} faces={visibleFaces} faceParts={faceParts} displacements={viewDisp} field={viewField}
              range={range} deformScale={view.deformScale} animate={view.animate} oscillate={oscillate}
              showEdges={view.showEdges} showUndeformed={view.showUndeformed} showMinMax={view.showMinMax}
              clip={view.clip} selectedNodes={selectedNodes} selectedFaces={selectedFaces} onPickFace={handlePickFace}
              onPickNode={handlePickNode} surfaceMid={visibleMid} faceSiblings={faceSiblings}
              probeMode={view.probeMode && !!display.field} onProbe={(v) => setProbeNode(tube ? tube.baseOf[v] : v)} probeNode={probeNode}
              pickNearest={lineModel} allowFacePick={!lineModel} ghosts={ghosts}
              onBoxSelect={handleBoxSelect} cullBack={!lineModel && project.element_type !== "S4"}
              glyphs={results ? [] : glyphs} refPoints={project.reference_points || []} theme={theme} pickMode={pickMode}
              setPickMode={(m) => { setPickMode(m); setView({ probeMode: false }); }}
              viewCommand={viewCommand} setView={setViewName} onApi={(a) => { viewApi.current = a; }}
            />
          ) : (
            <div className="empty-state"><p>Select or create a project (Project in the ribbon), then create or import geometry with the Geometry command, or open the ✦ AI Assistant and describe your problem.</p></div>
          )}
          {agentOpen && (
            <AgentPanel projectId={activeId} solving={solving} onClose={() => setAgentOpen(false)}
              onProject={handleAgentProject} onRun={handleAgentRun} />
          )}
          {guideOpen && (
            <GuidePanel project={project} job={job} solving={solving} isPro={isPro} onClose={() => setGuideOpen(false)} />
          )}
          {display.field && <Legend label={display.label} min={fmt(legendRange[0])} max={fmt(legendRange[1])} unit={display.unit} />}
        </main>
      </div>

      {workspace === "cfd" && (
        <div className="body">
          {project ? (
            <Suspense fallback={<div className="ws-pane-loading">Loading the CFD workspace…</div>}>
              <CfdPane key={`${project.id}:${cfdKey}`} projectId={project.id} projectName={project.name} onCase={onCfdCase} />
            </Suspense>
          ) : <div className="empty-state"><p>Select or create a project (Project in the ribbon) to set up CFD cases.</p></div>}
        </div>
      )}
      {workspace === "cad" && (
        <div className="body">
          {cadProjectId ? (
            <Suspense fallback={<div className="ws-pane-loading">Loading the CAD workspace…</div>}>
              <CadPane cadProjectId={cadProjectId} tab={cadTab} onTab={setCadTab} refreshKey={cadKey} />
            </Suspense>
          ) : <div className="empty-state"><p>{project ? "Opening the CAD workspace…" : "Select or create a project (Project in the ribbon) to start designing."}</p></div>}
        </div>
      )}

      <CommandModal open={openCmd === "project"} title="Project" icon="project" onClose={closeCmd}>
        <ProjectPanel projects={projects} activeId={activeId} onSelect={selectProject} onCreate={handleCreate}
          onDelete={handleDelete} onSave={handleSave} onOpen={handleOpen} onMerge={handleMerge} onExportInp={handleExportInp}
          history={history} onUndo={handleUndo} onRedo={handleRedo} workspace={workspace}
          summary={summary?.project_id === activeId ? summary : null} />
      </CommandModal>
      <CommandModal open={openCmd === "transfer"} title="Transfer between FEA · CFD · CAD" icon="transfer" onClose={closeCmd} width={380}>
        <BridgePanel open={openCmd === "transfer"} projectId={project?.id} workspace={workspace}
          cfdCaseId={workspace === "cfd" ? cfdCaseInUrl : null}
          onFea={handleBridgeFea} onCfd={handleBridgeCfd} onCad={handleBridgeCad} notify={notify} />
      </CommandModal>
      {project && (
        <CommandModal open={openCmd === "geometry"} title="Geometry & mesh" icon="geometry" onClose={closeCmd} width={360}>
          <GeometryPanel elementType={project.element_type} quality={quality} hasMesh={hasMesh}
            materials={project.materials} defaultMaterial={project.material} gmsh={caps?.gmsh}
            onGenerate={handleGenerate} onAppend={handleAppend} onImport={handleImportMesh} onImportCad={handleImportCad} />
        </CommandModal>
      )}
      {project && (
        <CommandModal open={openCmd === "structural"} title="Beams, trusses, shells, springs" icon="structural" onClose={closeCmd} width={360}>
          <StructuralPanel project={project} hasMesh={hasMesh} onGenerate={handleGenerateStructural}
            onAppend={handleAppendStructural} onCustom={handleCustomStructural} onMessage={notify} />
        </CommandModal>
      )}
      {hasMesh && (
        <CommandModal open={openCmd === "parts"} title={`Parts (${project.parts?.length || 0})`} icon="parts" onClose={closeCmd}>
          <PartsPanel parts={project.parts} defaultMaterial={project.material} materials={project.materials}
            hidden={hiddenParts} onToggle={togglePart} onUpdate={handlePartUpdate}
            onMove={handlePartMove} onDelete={handlePartDelete} structuralType={structuralType} />
        </CommandModal>
      )}
      {project && (
        <CommandModal open={openCmd === "materials"} title="Materials" icon="materials" onClose={closeCmd}>
          <MaterialPanel material={project.material} materials={project.materials} parts={project.parts}
            onUpdate={handleMaterialUpdate} onAdd={handleMaterialAdd} onDelete={handleMaterialDelete} />
        </CommandModal>
      )}
      {hasMesh && (
        <CommandModal open={openCmd === "selection"} title="Selection & sets" icon="selection" onClose={closeCmd}>
          <SelectionPanel nodes={project.nodes} faces={selectionFaces} selectedNodes={selectedNodes}
            selectedFaces={selectedFaces} setSelectedNodes={setSelectedNodes} setSelectedFaces={setSelectedFaces}
            angle={angle} setAngle={setAngle} sets={project.node_sets}
            onSaveSet={(name, kind, ids) => mutate(api.addNodeSet(activeId, { name, kind, ids }))}
            onDeleteSet={(id) => mutate(api.deleteNodeSet(activeId, id))} />
        </CommandModal>
      )}
      {project && (
        <CommandModal open={openCmd === "stock"} title="Stock setup (FEA model)" icon="stock" onClose={closeCmd}>
          <CamPanel project={project} quality={quality} />
        </CommandModal>
      )}

      <MessagesPanel messages={messages} open={msgOpen} setOpen={setMsgOpen} onClear={() => setMessages([])} />

      <div className="status-bar">
        <span className="status-item">{statusText}</span>
        {workspace === "fea" && job && (
          <>
            <span className="status-item">Job: {job.status}</span>
            {(job.status === "running" || job.status === "pending") && (
              <div className="status-progress"><div className="status-progress-fill" style={{ width: `${(job.progress || 0) * 100}%` }} /></div>
            )}
          </>
        )}
        {workspace === "fea" && hasMesh && <span className="status-item">{project.nodes.length.toLocaleString()} nodes · {project.elements.length.toLocaleString()} {project.element_type}</span>}
        {summary?.project_id === activeId && (
          <span className="status-item" title="What this project holds in each workspace">
            FEA {summary.fea.models} model{summary.fea.models === 1 ? "" : "s"} · CFD {summary.cfd.cases.length} case{summary.cfd.cases.length === 1 ? "" : "s"} · CAD {summary.cad.parts} part{summary.cad.parts === 1 ? "" : "s"}
          </span>
        )}
        <div className="spacer" />
        <span className="status-item">{ws?.label} · {ws?.name}</span>
        <span className="status-item">{project ? `${project.name}${workspace === "fea" && activeLayer ? ` · ${activeLayer.name}` : ""}` : "No project selected"}</span>
      </div>
    </div>
  );
}
