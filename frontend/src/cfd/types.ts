export type BoundaryType = "wall" | "inlet" | "outlet" | "symmetry";
export type Side = "left" | "right" | "bottom" | "top" | "back" | "front";
export type Dimension = "2d" | "3d";
export type BackendId = "native" | "openfoam" | "fenicsx" | "pyfr";
export type Turbulence = "laminar" | "k_epsilon" | "k_omega_sst";
export const SIDES_2D: Side[] = ["left", "right", "bottom", "top"];
export const SIDES_3D: Side[] = ["left", "right", "bottom", "top", "back", "front"];
/** Sides of the x-y view (also the four sides of a 2-D case). */
export const SIDES = SIDES_2D;
export const sidesOf = (c: { dimension: Dimension }): Side[] => (c.dimension === "3d" ? SIDES_3D : SIDES_2D);

export interface Material {
  name: string;
  density: number;
  viscosity: number;
  conductivity: number;
  specific_heat: number;
  thermal_expansion: number;
}
export interface Domain { width: number; height: number; depth: number }
export interface MeshSettings { nx: number; ny: number; nz: number }
export interface Models {
  energy: boolean;
  buoyancy: boolean;
  gravity_x: number;
  gravity_y: number;
  gravity_z: number;
  reference_temperature: number;
  turbulence: Turbulence;
}
export interface Obstacle {
  id: string;
  name: string;
  /** mesh = closed triangle surface (an FEA model or CAD part sent to CFD), vertices relative to (x, y, z) */
  shape: "rectangle" | "circle" | "sphere" | "mesh";
  x: number;
  y: number;
  z: number | null;
  width: number;
  height: number;
  depth: number | null;
  radius: number;
  thermal: "adiabatic" | "fixed";
  temperature: number;
  vertices?: number[][] | null;
  triangles?: number[][] | null;
  source?: { kind?: string; label?: string; ref_centre?: number[]; size?: number[] } | null;
}
export interface HeatSource {
  id: string;
  name: string;
  x: number;
  y: number;
  z: number | null;
  width: number;
  height: number;
  depth: number | null;
  power_density: number;
}
export interface Boundary {
  type: BoundaryType;
  velocity: number;
  profile: "uniform" | "parabolic";
  wall_velocity: number;
  thermal: "adiabatic" | "fixed";
  temperature: number;
}
export type Boundaries = Record<Side, Boundary>;
export interface Initial {
  velocity_x: number;
  velocity_y: number;
  velocity_z: number;
  pressure: number;
  temperature: number;
}
export interface RunSettings {
  mode: "steady" | "transient";
  end_time: number;
  max_iterations: number;
  cfl: number;
  tolerance: number;
  fixed_dt: number | null;
  convection_blend: number | null;
  monitor_interval: number;
  snapshot_count: number;
}
export interface Probe { id: string; name: string; x: number; y: number; z: number | null }
export interface SolverSettings { backend: BackendId; allow_fallback: boolean; order: number; processes: number }

export interface CaseConfig {
  dimension: Dimension;
  solver: SolverSettings;
  material: Material;
  domain: Domain;
  mesh: MeshSettings;
  models: Models;
  obstacles: Obstacle[];
  heat_sources: HeatSource[];
  boundaries: Boundaries;
  initial: Initial;
  run: RunSettings;
  probes: Probe[];
}

export interface Project {
  id: string;
  /** Gearhead project the case belongs to */
  project_id?: string | null;
  name: string;
  description: string;
  template: string | null;
  config: CaseConfig;
  created_at: string;
  updated_at: string;
}
export interface ProjectListItem {
  id: string;
  name: string;
  description: string;
  template: string | null;
  updated_at: string;
  created_at: string;
  nx: number;
  ny: number;
  nz: number;
  dimension: Dimension;
  backend: string;
  mode: string;
  energy: boolean;
  run_count: number;
  last_run_status: string | null;
}
export interface TemplateInfo { id: string; name: string; description: string }

export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export interface ObstacleLoad { Fx: number; Fy: number; Fz?: number; Q: number }
export interface RunSummaryData {
  iterations: number;
  sim_time: number;
  wall_time_s: number;
  converged: boolean;
  cells: number;
  fluid_cells: number;
  max_speed: number;
  p_min: number;
  p_max: number;
  mass_imbalance: number;
  reynolds_ref: number;
  T_min?: number;
  T_max?: number;
  pressure_drop?: number;
  obstacles?: Record<string, ObstacleLoad>;
  notes: string[];
  backend?: string;
  requested_backend?: string;
  dimension?: number;
}
export interface Run {
  id: string;
  project_id: string;
  name: string;
  status: RunStatus;
  progress: number;
  iteration: number;
  sim_time: number;
  converged: boolean;
  error: string | null;
  summary: RunSummaryData | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
export interface RunDetail extends Run { config: CaseConfig }

export interface MonitorSample {
  iteration: number;
  time: number;
  residuals: Record<string, number>;
  values: Record<string, number>;
}
export interface LogLine { id: number; ts: string; level: "info" | "warning" | "error"; message: string }

export interface CheckItem { level: "error" | "warning" | "info"; section: string; message: string }
export interface CheckResult { ok: boolean; items: CheckItem[]; stats: Record<string, number> }

export type FieldName = "speed" | "u" | "v" | "w" | "p" | "T" | "vorticity";
export interface SlicePlane { axis: 0 | 1 | 2; normal: "x" | "y" | "z"; index: number; count: number; position: number; axes: string[] }
export interface FieldsPayload {
  nx: number;
  ny: number;
  lx: number;
  ly: number;
  axes: [string, string];
  vectors: [FieldName, FieldName];
  dim: 2 | 3;
  plane: SlicePlane | null;
  backend: string;
  energy: boolean;
  time: number;
  frame: number;
  n_frames: number;
  iteration: number | null;
  mask: string;
  fields: Partial<Record<FieldName, string>>;
  ranges: Partial<Record<FieldName, [number, number]>>;
}
export interface ProbeLine {
  distance: number[];
  x: number[];
  y: number[];
  time: number;
  values: Partial<Record<FieldName, number[]>>;
}

export interface SolverInfo {
  id: string;
  label: string;
  description: string;
  capabilities: { dims: number[]; steady: boolean; transient: boolean; energy: boolean; buoyancy: boolean; turbulence: boolean };
  available: { ok: boolean; mode: string; detail: string };
  fallback: string | null;
  fallback_label?: string;
  exportable: boolean;
  selectable: boolean;
}
