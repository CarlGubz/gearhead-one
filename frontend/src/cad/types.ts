export type Vec3 = [number, number, number];
export type Json = any;

export interface ParamDef {
  name: string;
  label: string;
  type: string;
  default: Json;
  options?: Json[];
}
export interface FeatureSchema { label: string; group: string; params: ParamDef[] }
export interface OpSchema { label: string; params: ParamDef[] }

export interface Meta {
  features: Record<string, FeatureSchema>;
  materials: Record<string, { label: string; density: number; E: number; nu: number; yield_strength: number; color: string }>;
  planes: string[];
  sketch_entity_types: string[];
  constraint_types: { type: string; refs: string[]; value?: boolean; help?: string }[];
  sketch_ops: string[];
  sketch_edit_ops?: string[];
  mate_types: { type: string; params: string[] }[];
  cam_operations: Record<string, OpSchema>;
  cam_default_tools: Tool[];
  post_defaults: Record<string, Json>;
  post_dialects: string[];
  simulation_kinds: string[];
  calculix?: { available: boolean; solver: string | null; hint: string | null; kinds: Record<string, string> };
}

export interface Project {
  id: number;
  name: string;
  description: string;
  counts?: { sketches: number; parts: number; assemblies: number };
}

export interface Entity { id: string; type: string; construction?: boolean; [k: string]: Json }
export interface Constraint { type: string; a?: string; b?: string; c?: string; value?: Json; [k: string]: Json }
export interface Layer { name: string; color: string | null; visible: boolean; locked: boolean }
export interface Sketch {
  id: number; project_id: number; name: string; plane: string; offset: number;
  entities: Entity[]; constraints: Constraint[]; layers?: Layer[];
}
export interface SolveResult {
  status: string; dof: number; max_residual: number | null; iterations?: number; message?: string; entities: Entity[];
}

export interface Feature { id: string; type: string; name: string; suppressed: boolean; params: Record<string, Json> }
export interface FeatureStatus { id: string; ok: boolean; error: string | null; volume?: number; suppressed?: boolean; warnings?: string[] }
export interface Part {
  id: number; project_id: number; name: string; material: string; color: string;
  parameters: Record<string, Json>; features: Feature[]; statuses?: FeatureStatus[];
}

export interface FaceMeta { index: number; type: string; area: number; center: Vec3; normal: Vec3; tri_start: number; tri_count: number }
export interface EdgeMeta { index: number; type: string; length: number; points: number[] }
export interface MeshData { positions: number[]; normals: number[]; indices: number[]; faces: FaceMeta[]; edges: EdgeMeta[] }
export interface MassProps {
  volume_mm3: number; area_mm2: number; mass_kg: number; density_kg_m3: number; center_of_mass: Vec3;
  bbox_min: Vec3; bbox_max: Vec3; bbox_size: Vec3; faces: number; edges: number; solids: number;
}

export interface Component {
  id: number; assembly_id: number; part_id: number; name: string;
  position: Vec3; rotation: Vec3; fixed: boolean;
  color?: string; matrix?: number[]; has_geometry?: boolean;
}
export interface Mate { id: number; assembly_id: number; type: string; comp_a: number; comp_b: number; params: Record<string, Json> }
export interface Assembly { id: number; project_id: number; name: string; components: Component[]; mates: Mate[] }

export interface Tool {
  id: number; name: string; type: string; diameter: number; flutes?: number; rpm: number; feed: number; plunge_feed: number;
  /** holder / shank envelope used by the collision check (defaults are derived from the diameter) */
  flute_length?: number; stickout?: number; shank_diameter?: number; holder_diameter?: number;
}
export interface CamOp { id: string; type: string; name?: string; enabled: boolean; params: Record<string, Json> }
export interface CamSetup {
  id: number; part_id: number; name: string; stock: Json; tools: Tool[]; operations: CamOp[]; post: Record<string, Json>;
  summary: Json; has_gcode: boolean; gcode?: string;
}
export interface Toolpath { op: string; kind: "rapid" | "cut" | "plunge"; pts: number[] }

export interface DrawingView { id: string; part_id: number; kind: string; scale: number; x: number; y: number; center: [number, number]; hidden: boolean; label: string; show_scale?: boolean }
export interface Drawing {
  id: number; project_id: number; name: string;
  sheet: { size: string; projection: string; decimals: number; units: string; orientation?: string };
  views: DrawingView[]; dimensions: Json[]; notes: { x: number; y: number; text: string; size?: number }[]; title: Record<string, string>;
}
export interface Snap { edge: number; end: 0 | 1 | "c"; p: number[]; r?: number; sx: number; sy: number }
export interface DrawingRender {
  sheet: { w: number; h: number };
  primitives: Json[];
  views: { id: string; box?: [number, number, number, number]; snaps?: Snap[]; missing?: boolean }[];
  dimensions: { id: string; ok: boolean; value: number | null; error?: string }[];
}

export interface SimJob {
  id: number; part_id: number | null; assembly_id?: number | null; kind: string; config: Json; status: string; error: string;
  summary: Json; mesh: Json; result?: Json;
}
