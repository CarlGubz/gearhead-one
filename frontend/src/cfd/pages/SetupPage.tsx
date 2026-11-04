import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";
import { useProject } from "../ProjectContext";
import { Field, NumberInput, Segmented, Toggle } from "../components/ui";
import { GeometryCanvas, type Selection } from "../components/GeometryCanvas";
import { nextId } from "../lib/geometry";
import { formatNumber } from "../lib/fields";
import type { BackendId, Boundary, BoundaryType, CaseConfig, CheckItem, HeatSource, Material, Obstacle, Probe, Side, SolverInfo, Turbulence } from "../types";
import { SIDES, sidesOf } from "../types";

type Section = "general" | "geometry" | "mesh" | "models" | "materials" | "boundaries" | "init" | "run" | "monitors";
const NAV: { group: string; items: { id: Section; label: string; icon: string }[] }[] = [
  { group: "Problem", items: [
    { id: "general", label: "General", icon: "◧" },
    { id: "geometry", label: "Geometry", icon: "▭" },
    { id: "mesh", label: "Mesh", icon: "▦" },
  ] },
  { group: "Physics", items: [
    { id: "models", label: "Models", icon: "∂" },
    { id: "materials", label: "Materials", icon: "◍" },
    { id: "boundaries", label: "Boundary conditions", icon: "⇥" },
    { id: "init", label: "Initialization", icon: "⟲" },
  ] },
  { group: "Solution", items: [
    { id: "run", label: "Run conditions", icon: "▶" },
    { id: "monitors", label: "Monitors", icon: "⌖" },
  ] },
];

const MATERIAL_PRESETS: Record<string, Material> = {
  air: { name: "Air (20 °C)", density: 1.204, viscosity: 1.825e-5, conductivity: 0.0257, specific_heat: 1005, thermal_expansion: 3.43e-3 },
  water: { name: "Water (20 °C)", density: 998.2, viscosity: 1.002e-3, conductivity: 0.598, specific_heat: 4182, thermal_expansion: 2.07e-4 },
  oil: { name: "Engine oil (40 °C)", density: 876, viscosity: 0.0728, conductivity: 0.144, specific_heat: 1964, thermal_expansion: 7e-4 },
  unit: { name: "Non-dimensional (ρ = μ = k = cp = 1)", density: 1, viscosity: 1, conductivity: 1, specific_heat: 1, thermal_expansion: 0 },
};

const SECTION_OF_CHECK: Record<string, Section> = {
  mesh: "mesh", geometry: "geometry", boundaries: "boundaries", models: "models", physics: "materials", run: "run", monitors: "monitors",
};

function Card({ children, sel, onClick }: { children: ReactNode; sel?: boolean; onClick?: () => void }) {
  return <div className={`subcard ${sel ? "sel" : ""}`} onClick={onClick}>{children}</div>;
}

export default function SetupPage() {
  const { project, config, setConfig, rename, save, check } = useProject();
  const [section, setSection] = useState<Section>("general");
  const [sel, setSel] = useState<Selection>(null);
  const [showGrid, setShowGrid] = useState(true);

  const counts = useMemo(() => {
    const c: Partial<Record<Section, { e: number; w: number }>> = {};
    for (const i of check?.items ?? []) {
      const s = SECTION_OF_CHECK[i.section] ?? "general";
      c[s] ??= { e: 0, w: 0 };
      if (i.level === "error") c[s]!.e++;
      if (i.level === "warning") c[s]!.w++;
    }
    return c;
  }, [check]);

  const patch = <K extends keyof CaseConfig>(key: K, v: Partial<CaseConfig[K]>) =>
    setConfig((c) => ({ ...c, [key]: { ...(c[key] as object), ...(v as object) } }));

  const onMove = (s: NonNullable<Selection>, x: number, y: number) =>
    setConfig((c) => {
      if (s.kind === "obstacle") return { ...c, obstacles: c.obstacles.map((o) => (o.id === s.id ? { ...o, x, y } : o)) };
      if (s.kind === "heat") return { ...c, heat_sources: c.heat_sources.map((o) => (o.id === s.id ? { ...o, x, y } : o)) };
      return { ...c, probes: c.probes.map((o) => (o.id === s.id ? { ...o, x, y } : o)) };
    });

  const onSelect = (s: Selection) => {
    setSel(s);
    if (s?.kind === "probe") setSection("monitors");
    else if (s) setSection("geometry");
  };

  return (
    <div className="workspace">
      <nav className="sidenav">
        {NAV.map((g) => (
          <div key={g.group}>
            <div className="group">{g.group}</div>
            {g.items.map((it) => (
              <button key={it.id} className={`navitem ${section === it.id ? "on" : ""}`} onClick={() => setSection(it.id)}>
                <span style={{ width: 16, textAlign: "center" }}>{it.icon}</span>
                {it.label}
                {counts[it.id]?.e ? <span className="badge">{counts[it.id]!.e}</span> : counts[it.id]?.w ? <span className="badge w">{counts[it.id]!.w}</span> : null}
              </button>
            ))}
          </div>
        ))}
      </nav>

      <div className="formpane">
        {save.state === "error" && (
          <div className="banner err" style={{ marginBottom: 14 }}>
            <b>Changes could not be saved.</b>
            <ul>{save.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
          </div>
        )}
        {section === "general" && (
          <div className="stack">
            <div><h2>General</h2><p className="lead">Name your case and choose how it is solved.</p></div>
            <Field label="Project name"><input type="text" value={project.name} onChange={(e) => rename(e.target.value, project.description)} /></Field>
            <Field label="Description"><textarea value={project.description} onChange={(e) => rename(project.name, e.target.value)} /></Field>
            <Field label="Dimension" hint={config.dimension === "3d" ? "3-D box domain with front/back sides; results are viewed as slices and exported as hexahedra." : "2-D domain, 1 m deep — forces are per metre of depth."}>
              <Segmented value={config.dimension} onChange={(dimension) => setConfig((c) => ({ ...c, dimension }))} options={[{ value: "2d", label: "2-D" }, { value: "3d", label: "3-D" }]} />
            </Field>
            <Field label="Analysis type" hint={config.run.mode === "steady" ? "Marches in pseudo-time until the residuals fall below the tolerance." : "Resolves the unsteady flow up to the end time and stores animation frames."}>
              <Segmented value={config.run.mode} onChange={(mode) => patch("run", { mode })} options={[{ value: "steady", label: "Steady-state" }, { value: "transient", label: "Transient" }]} />
            </Field>
            <SolverSection config={config} patch={patch} projectId={project.id} />
            <div className="banner warn small">
              Incompressible Newtonian fluid with constant properties and Boussinesq buoyancy.
              {config.models.turbulence === "laminar" ? " Laminar flow" : " RANS turbulence (OpenFOAM)"}{config.dimension === "3d" ? " in 3-D" : " in 2-D"}.
            </div>
          </div>
        )}

        {section === "geometry" && <Geometry config={config} setConfig={setConfig} patch={patch} sel={sel} setSel={setSel} />}
        {section === "mesh" && <MeshSection config={config} patch={patch} stats={check?.stats} />}

        {section === "models" && (
          <div className="stack">
            <div><h2>Models</h2><p className="lead">Choose the physics to solve besides the flow itself.</p></div>
            <Toggle checked={config.models.energy} onChange={(energy) => patch("models", { energy, buoyancy: energy ? config.models.buoyancy : false })} label="Energy (heat transfer)" hint="Solves the temperature field: forced / natural convection, fixed-temperature walls and obstacles, heat sources." />
            <Toggle checked={config.models.buoyancy} onChange={(buoyancy) => patch("models", { buoyancy })} label="Buoyancy (Boussinesq)" hint="Density varies linearly with temperature in the gravity term only." />
            {!config.models.energy && <div className="muted small">Enable the energy equation to unlock buoyancy.</div>}
            {config.models.energy && config.models.buoyancy && (
              <div className="grid3">
                <Field label="Gravity x"><NumberInput value={config.models.gravity_x} unit="m/s²" onChange={(gravity_x) => patch("models", { gravity_x })} /></Field>
                <Field label="Gravity y"><NumberInput value={config.models.gravity_y} unit="m/s²" onChange={(gravity_y) => patch("models", { gravity_y })} /></Field>
                {config.dimension === "3d" && <Field label="Gravity z"><NumberInput value={config.models.gravity_z} unit="m/s²" onChange={(gravity_z) => patch("models", { gravity_z })} /></Field>}
                <Field label="Reference T"><NumberInput value={config.models.reference_temperature} unit="K" onChange={(reference_temperature) => patch("models", { reference_temperature })} /></Field>
              </div>
            )}
            <Field label="Turbulence" hint={config.solver.backend === "openfoam" ? "RANS with standard wall functions; inlet turbulence from 5 % intensity and a 0.07 Dh mixing length." : "RANS models are solved by the OpenFOAM backend (General → Solver). The built-in solvers are laminar."}>
              <select value={config.models.turbulence} onChange={(e) => patch("models", { turbulence: e.target.value as Turbulence })}>
                <option value="laminar">Laminar</option>
                <option value="k_epsilon">k-ε (OpenFOAM)</option>
                <option value="k_omega_sst">k-ω SST (OpenFOAM)</option>
              </select>
            </Field>
          </div>
        )}

        {section === "materials" && <MaterialSection config={config} patch={patch} />}
        {section === "boundaries" && <Boundaries config={config} setConfig={setConfig} />}

        {section === "init" && (
          <div className="stack">
            <div><h2>Initialization</h2><p className="lead">Starting fields. A good initial guess (e.g. inlet speed) speeds up convergence; transient runs start from here.</p></div>
            <div className={config.dimension === "3d" ? "grid3" : "grid2"}>
              <Field label="Velocity x"><NumberInput value={config.initial.velocity_x} unit="m/s" onChange={(velocity_x) => patch("initial", { velocity_x })} /></Field>
              <Field label="Velocity y"><NumberInput value={config.initial.velocity_y} unit="m/s" onChange={(velocity_y) => patch("initial", { velocity_y })} /></Field>
              {config.dimension === "3d" && <Field label="Velocity z"><NumberInput value={config.initial.velocity_z} unit="m/s" onChange={(velocity_z) => patch("initial", { velocity_z })} /></Field>}
            </div>
            {config.models.energy && <Field label="Temperature"><NumberInput value={config.initial.temperature} unit="K" onChange={(temperature) => patch("initial", { temperature })} /></Field>}
            <button className="btn sm" onClick={() => {
              const inlet = SIDES.map((s) => config.boundaries[s]).find((b) => b.type === "inlet");
              const left = config.boundaries.left;
              if (inlet) patch("initial", { velocity_x: left.type === "inlet" ? left.velocity : config.initial.velocity_x });
            }}>Set from left inlet</button>
          </div>
        )}

        {section === "run" && <RunSection config={config} patch={patch} stats={check?.stats} />}
        {section === "monitors" && <Monitors config={config} setConfig={setConfig} sel={sel} setSel={setSel} />}
      </div>

      <div className="previewpane">
        <div className="previewbar">
          <Toggle checked={showGrid} onChange={setShowGrid} label="Mesh" />
          <div className="legendrow">
            <span><i style={{ background: "#6b7785" }} />wall</span>
            <span><i style={{ background: "#1d7fd6" }} />inlet</span>
            <span><i style={{ background: "#1f9d62" }} />outlet</span>
            <span><i style={{ background: "#8b5cf6" }} />symmetry</span>
            <span><i style={{ background: "#e0452f" }} />hot/cold solid</span>
            <span><i style={{ background: "#d946ef" }} />probe</span>
          </div>
          <span className="muted small grow" style={{ textAlign: "right" }}>{config.dimension === "3d" ? "x–y view (projection along z) · " : ""}Drag obstacles, sources and probes</span>
        </div>
        <GeometryCanvas config={config} selection={sel} onSelect={onSelect} onMove={onMove} showGrid={showGrid} />
        <Checks items={check?.items ?? []} />
      </div>
    </div>
  );
}

function Checks({ items }: { items: CheckItem[] }) {
  const order = { error: 0, warning: 1, info: 2 } as const;
  const sorted = [...items].sort((a, b) => order[a.level] - order[b.level]);
  const bad = items.filter((i) => i.level === "error").length;
  return (
    <div className="checks">
      <div className="row between" style={{ marginBottom: 4 }}>
        <h3>Case check</h3>
        <span className={`chip ${bad ? "failed" : "completed"}`}>{bad ? `${bad} error${bad > 1 ? "s" : ""} — fix before running` : "ready to run"}</span>
      </div>
      {sorted.length === 0 && <div className="muted small">No issues found.</div>}
      {sorted.map((i, k) => (
        <div key={k} className={`check ${i.level}`}>
          <span className="ic">{i.level === "error" ? "!" : i.level === "warning" ? "▲" : "i"}</span>
          <span><span className="sec">{i.section}</span>{i.message}</span>
        </div>
      ))}
    </div>
  );
}

type Patch = <K extends keyof CaseConfig>(key: K, v: Partial<CaseConfig[K]>) => void;
type SetConfig = (fn: (c: CaseConfig) => CaseConfig) => void;

function Geometry({ config, setConfig, patch, sel, setSel }: { config: CaseConfig; setConfig: SetConfig; patch: Patch; sel: Selection; setSel: (s: Selection) => void }) {
  const { width, height, depth } = config.domain;
  const is3d = config.dimension === "3d";
  const shapeName = { rectangle: is3d ? "Box" : "Block", circle: "Cylinder", sphere: "Sphere", mesh: "Solid" } as const;
  const addObstacle = (shape: Obstacle["shape"]) =>
    setConfig((c) => {
      const id = nextId("obs", c.obstacles);
      const o: Obstacle = { id, name: `${shapeName[shape]} ${c.obstacles.length + 1}`, shape, x: width / 2, y: height / 2, z: null, width: width / 8, height: height / 4, depth: null, radius: Math.min(width, height) / 8, thermal: "adiabatic", temperature: 323.15 };
      setSel({ kind: "obstacle", id });
      return { ...c, obstacles: [...c.obstacles, o] };
    });
  const upO = (id: string, v: Partial<Obstacle>) => setConfig((c) => ({ ...c, obstacles: c.obstacles.map((o) => (o.id === id ? { ...o, ...v } : o)) }));
  const upH = (id: string, v: Partial<HeatSource>) => setConfig((c) => ({ ...c, heat_sources: c.heat_sources.map((o) => (o.id === id ? { ...o, ...v } : o)) }));
  const zFields = (item: { z: number | null; depth?: number | null }, up: (v: { z?: number | null; depth?: number | null }) => void, withDepth: boolean) => is3d && (
    <div className="grid2" style={{ marginTop: 10 }}>
      <Field label="Centre z" hint="Empty = mid-depth"><NumberInput value={item.z} unit="m" allowEmpty placeholder={formatNumber(depth / 2, 4)} onEmpty={() => up({ z: null })} onChange={(z) => up({ z })} /></Field>
      {withDepth && <Field label="Depth (z)" hint="Empty = full domain depth"><NumberInput value={item.depth ?? null} unit="m" min={1e-9} allowEmpty placeholder="full" onEmpty={() => up({ depth: null })} onChange={(d) => up({ depth: d })} /></Field>}
    </div>
  );
  return (
    <div className="stack">
      <div><h2>Geometry</h2><p className="lead">A rectangular {is3d ? "3-D box" : "2-D"} domain with solid obstacles (no-slip) and optional volumetric heat sources.</p></div>
      <div className={is3d ? "grid3" : "grid2"}>
        <Field label="Domain width (x)"><NumberInput value={width} unit="m" min={1e-6} onChange={(v) => patch("domain", { width: v })} /></Field>
        <Field label="Domain height (y)"><NumberInput value={height} unit="m" min={1e-6} onChange={(v) => patch("domain", { height: v })} /></Field>
        {is3d && <Field label="Domain depth (z)"><NumberInput value={depth} unit="m" min={1e-6} onChange={(v) => patch("domain", { depth: v })} /></Field>}
      </div>
      <div className="row between"><h3>Obstacles</h3>
        <span className="row">
          <button className="btn sm" onClick={() => addObstacle("rectangle")}>＋ {is3d ? "Box" : "Rectangle"}</button>
          <button className="btn sm" onClick={() => addObstacle("circle")}>＋ {is3d ? "Cylinder (z)" : "Circle"}</button>
          {is3d && <button className="btn sm" onClick={() => addObstacle("sphere")}>＋ Sphere</button>}
        </span></div>
      {config.obstacles.length === 0 && <div className="muted small">No obstacles — an empty domain.</div>}
      {config.obstacles.map((o) => (
        <Card key={o.id} sel={sel?.kind === "obstacle" && sel.id === o.id} onClick={() => setSel({ kind: "obstacle", id: o.id })}>
          <header>
            <input type="text" value={o.name} onChange={(e) => upO(o.id, { name: e.target.value })} />
            <button className="btn sm ghost danger" onClick={() => setConfig((c) => ({ ...c, obstacles: c.obstacles.filter((x) => x.id !== o.id) }))}>Remove</button>
          </header>
          <div className="grid2">
            <Field label="Centre x"><NumberInput value={o.x} unit="m" onChange={(x) => upO(o.id, { x })} /></Field>
            <Field label="Centre y"><NumberInput value={o.y} unit="m" onChange={(y) => upO(o.id, { y })} /></Field>
            {o.shape === "mesh" ? (
              <div className="small muted" style={{ gridColumn: "1 / -1" }}>
                Imported solid{o.source?.label ? <> from <b>{o.source.kind?.startsWith("cad") ? "CAD" : "FEA"} · {o.source.label}</b></> : null} —{" "}
                {o.triangles?.length ?? 0} triangles, {(o.source?.size ?? []).map((v) => formatNumber(v, 3)).join(" × ")} m.
                {" "}{is3d ? "The canvas shows its section through the centre; the solver uses the whole body." : "The 2-D solver uses its section through the centre."}
                {" "}Move it with the centre fields or by dragging; edit its shape in the FEA or CAD workspace and send it again.
              </div>
            ) : o.shape === "rectangle" ? (
              <>
                <Field label="Width"><NumberInput value={o.width} unit="m" min={1e-9} onChange={(width) => upO(o.id, { width })} /></Field>
                <Field label="Height"><NumberInput value={o.height} unit="m" min={1e-9} onChange={(height) => upO(o.id, { height })} /></Field>
              </>
            ) : (
              <Field label="Radius"><NumberInput value={o.radius} unit="m" min={1e-9} onChange={(radius) => upO(o.id, { radius })} /></Field>
            )}
          </div>
          {zFields(o, (v) => upO(o.id, v), o.shape !== "sphere" && o.shape !== "mesh")}
          {config.models.energy && (
            <div className="grid2" style={{ marginTop: 10 }}>
              <Field label="Thermal condition"><select value={o.thermal} onChange={(e) => upO(o.id, { thermal: e.target.value as Obstacle["thermal"] })}><option value="adiabatic">Adiabatic (insulated)</option><option value="fixed">Fixed temperature</option></select></Field>
              {o.thermal === "fixed" && <Field label="Temperature"><NumberInput value={o.temperature} unit="K" onChange={(temperature) => upO(o.id, { temperature })} /></Field>}
            </div>
          )}
        </Card>
      ))}
      {config.models.energy && (
        <>
          <div className="row between"><h3>Heat sources</h3>
            <button className="btn sm" onClick={() => setConfig((c) => {
              const id = nextId("src", c.heat_sources);
              setSel({ kind: "heat", id });
              return { ...c, heat_sources: [...c.heat_sources, { id, name: `Source ${c.heat_sources.length + 1}`, x: width / 2, y: height / 2, z: null, width: width / 8, height: height / 4, depth: null, power_density: 1e4 }] };
            })}>＋ Add</button></div>
          {config.heat_sources.length === 0 && <div className="muted small">Volumetric heat generation inside the fluid (W/m³).</div>}
          {config.heat_sources.map((h) => (
            <Card key={h.id} sel={sel?.kind === "heat" && sel.id === h.id} onClick={() => setSel({ kind: "heat", id: h.id })}>
              <header><input type="text" value={h.name} onChange={(e) => upH(h.id, { name: e.target.value })} />
                <button className="btn sm ghost danger" onClick={() => setConfig((c) => ({ ...c, heat_sources: c.heat_sources.filter((x) => x.id !== h.id) }))}>Remove</button></header>
              <div className="grid2">
                <Field label="Centre x"><NumberInput value={h.x} unit="m" onChange={(x) => upH(h.id, { x })} /></Field>
                <Field label="Centre y"><NumberInput value={h.y} unit="m" onChange={(y) => upH(h.id, { y })} /></Field>
                <Field label="Width"><NumberInput value={h.width} unit="m" min={1e-9} onChange={(width) => upH(h.id, { width })} /></Field>
                <Field label="Height"><NumberInput value={h.height} unit="m" min={1e-9} onChange={(height) => upH(h.id, { height })} /></Field>
              </div>
              {zFields(h, (v) => upH(h.id, v), true)}
              <div style={{ marginTop: 10 }}><Field label="Power density"><NumberInput value={h.power_density} unit="W/m³" onChange={(power_density) => upH(h.id, { power_density })} /></Field></div>
            </Card>
          ))}
        </>
      )}
    </div>
  );
}

function MeshSection({ config, patch, stats }: { config: CaseConfig; patch: Patch; stats?: Record<string, number> }) {
  const [square, setSquare] = useState(true);
  const { width, height, depth } = config.domain;
  const is3d = config.dimension === "3d";
  const fit = (n: number, from: number, to: number) => Math.max(4, Math.round((n * to) / from));
  const setNx = (nx: number) => patch("mesh", square ? { nx, ny: fit(nx, width, height), nz: fit(nx, width, depth) } : { nx });
  const setNy = (ny: number) => patch("mesh", square ? { ny, nx: fit(ny, height, width), nz: fit(ny, height, depth) } : { ny });
  const setNz = (nz: number) => patch("mesh", square ? { nz, nx: fit(nz, depth, width), ny: fit(nz, depth, height) } : { nz });
  const preset = (n: number) => {
    const long = Math.max(width, height, is3d ? depth : 0);
    patch("mesh", { nx: fit(n, long, width), ny: fit(n, long, height), nz: fit(n, long, depth) });
  };
  const presets: [string, number][] = is3d ? [["Coarse", 24], ["Medium", 40], ["Fine", 64], ["Very fine", 96]] : [["Coarse", 40], ["Medium", 80], ["Fine", 160], ["Very fine", 320]];
  return (
    <div className="stack">
      <div><h2>Mesh</h2><p className="lead">Uniform Cartesian {is3d ? "hexahedral" : ""} cells; obstacles are cut out as staircase solid cells
        {config.solver.backend === "openfoam" ? " (OpenFOAM: blockMesh + topoSet/subsetMesh, the same cells)." : "."}</p></div>
      <div className="row wrap"><span className="muted small">Resolution:</span>
        {presets.map(([l, n]) => <button key={l} className="btn sm" onClick={() => preset(n)}>{l}</button>)}</div>
      <div className={is3d ? "grid3" : "grid2"}>
        <Field label="Cells in x"><NumberInput value={config.mesh.nx} integer min={4} max={1000} onChange={setNx} /></Field>
        <Field label="Cells in y"><NumberInput value={config.mesh.ny} integer min={4} max={1000} onChange={setNy} /></Field>
        {is3d && <Field label="Cells in z"><NumberInput value={config.mesh.nz} integer min={4} max={1000} onChange={setNz} /></Field>}
      </div>
      <Toggle checked={square} onChange={setSquare} label="Keep cells cubic" hint="Changing one count updates the others from the domain aspect ratio." />
      {stats && (
        <dl className="kv subcard">
          <dt>Total cells</dt><dd>{stats.cells?.toLocaleString()}</dd>
          <dt>Fluid cells</dt><dd>{stats.fluid_cells?.toLocaleString()}</dd>
          <dt>Cell size</dt><dd>{formatNumber(stats.dx, 3)} × {formatNumber(stats.dy, 3)}{stats.dz ? ` × ${formatNumber(stats.dz, 3)}` : ""} m</dd>
          <dt>Aspect ratio</dt><dd>{formatNumber(stats.aspect_ratio, 3)}</dd>
          <dt>Solid fraction</dt><dd>{(stats.solid_fraction * 100).toFixed(1)} %</dd>
          <dt>Est. speed</dt><dd>~{formatNumber(stats.steps_per_second_estimate, 3)} steps/s</dd>
        </dl>
      )}
    </div>
  );
}

function MaterialSection({ config, patch }: { config: CaseConfig; patch: Patch }) {
  const m = config.material;
  const set = (v: Partial<Material>) => patch("material", { ...v, name: v.name ?? "Custom" });
  const nu = m.viscosity / m.density, alpha = m.conductivity / (m.density * m.specific_heat);
  return (
    <div className="stack">
      <div><h2>Materials</h2><p className="lead">Constant-property Newtonian fluid.</p></div>
      <Field label="Preset"><select value="" onChange={(e) => e.target.value && patch("material", MATERIAL_PRESETS[e.target.value])}>
        <option value="">{m.name}</option>
        {Object.entries(MATERIAL_PRESETS).map(([k, v]) => <option key={k} value={k}>{v.name}</option>)}
      </select></Field>
      <div className="grid2">
        <Field label="Density ρ"><NumberInput value={m.density} unit="kg/m³" min={1e-12} onChange={(density) => set({ density })} /></Field>
        <Field label="Dynamic viscosity μ"><NumberInput value={m.viscosity} unit="Pa·s" min={1e-15} onChange={(viscosity) => set({ viscosity })} /></Field>
        {config.models.energy && (
          <>
            <Field label="Conductivity k"><NumberInput value={m.conductivity} unit="W/m·K" min={1e-12} onChange={(conductivity) => set({ conductivity })} /></Field>
            <Field label="Specific heat cp"><NumberInput value={m.specific_heat} unit="J/kg·K" min={1e-12} onChange={(specific_heat) => set({ specific_heat })} /></Field>
            {config.models.buoyancy && <Field label="Expansion β"><NumberInput value={m.thermal_expansion} unit="1/K" min={0} onChange={(thermal_expansion) => set({ thermal_expansion })} /></Field>}
          </>
        )}
      </div>
      <dl className="kv subcard">
        <dt>Kinematic viscosity ν</dt><dd>{formatNumber(nu, 4)} m²/s</dd>
        {config.models.energy && <><dt>Thermal diffusivity α</dt><dd>{formatNumber(alpha, 4)} m²/s</dd><dt>Prandtl number</dt><dd>{formatNumber(nu / alpha, 4)}</dd></>}
      </dl>
    </div>
  );
}

function Boundaries({ config, setConfig }: { config: CaseConfig; setConfig: SetConfig }) {
  const up = (side: Side, v: Partial<Boundary>) => setConfig((c) => ({ ...c, boundaries: { ...c.boundaries, [side]: { ...c.boundaries[side], ...v } } }));
  const dirLabel = (s: Side) => (s === "left" || s === "right" ? "+y (up)" : "+x (right)");
  const where: Partial<Record<Side, string>> = { back: "z = 0", front: "z = depth" };
  return (
    <div className="stack">
      <div><h2>Boundary conditions</h2><p className="lead">One condition per side of the domain. Outlets are held at 0 Pa gauge.</p></div>
      {sidesOf(config).map((side) => {
        const b = config.boundaries[side];
        return (
          <Card key={side}>
            <header><h3 style={{ textTransform: "capitalize" }}>{side}{where[side] && <span className="muted small"> ({where[side]})</span>}</h3>
              <select style={{ width: "auto", marginLeft: "auto" }} value={b.type} onChange={(e) => up(side, { type: e.target.value as BoundaryType, velocity: e.target.value === "inlet" && b.velocity <= 0 ? 1 : b.velocity })}>
                <option value="wall">Wall</option><option value="inlet">Velocity inlet</option><option value="outlet">Pressure outlet</option><option value="symmetry">Symmetry</option>
              </select></header>
            <div className="stack" style={{ gap: 10 }}>
              {b.type === "inlet" && (
                <div className="grid2">
                  <Field label="Mean speed (into domain)"><NumberInput value={b.velocity} unit="m/s" min={0} onChange={(velocity) => up(side, { velocity })} /></Field>
                  <Field label="Profile"><select value={b.profile} onChange={(e) => up(side, { profile: e.target.value as Boundary["profile"] })}><option value="uniform">Uniform</option><option value="parabolic">Parabolic (laminar)</option></select></Field>
                </div>
              )}
              {b.type === "wall" && <Field label={`Moving-wall speed (${dirLabel(side)})`} hint="0 = stationary no-slip wall"><NumberInput value={b.wall_velocity} unit="m/s" onChange={(wall_velocity) => up(side, { wall_velocity })} /></Field>}
              {config.models.energy && b.type === "wall" && (
                <div className="grid2">
                  <Field label="Thermal"><select value={b.thermal} onChange={(e) => up(side, { thermal: e.target.value as Boundary["thermal"] })}><option value="adiabatic">Adiabatic</option><option value="fixed">Fixed temperature</option></select></Field>
                  {b.thermal === "fixed" && <Field label="Temperature"><NumberInput value={b.temperature} unit="K" onChange={(temperature) => up(side, { temperature })} /></Field>}
                </div>
              )}
              {config.models.energy && b.type === "inlet" && <Field label="Inlet temperature"><NumberInput value={b.temperature} unit="K" onChange={(temperature) => up(side, { temperature })} /></Field>}
              {b.type === "outlet" && <div className="muted small">Zero-gradient velocity{config.models.energy ? " and temperature" : ""}, p = 0.</div>}
              {b.type === "symmetry" && <div className="muted small">No flow through, free slip, adiabatic.</div>}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function RunSection({ config, patch, stats }: { config: CaseConfig; patch: Patch; stats?: Record<string, number> }) {
  const r = config.run;
  return (
    <div className="stack">
      <div><h2>Run conditions</h2><p className="lead">Time stepping and stopping criteria. Defaults are stable for most cases.</p></div>
      <Segmented value={r.mode} onChange={(mode) => patch("run", { mode })} options={[{ value: "steady", label: "Steady-state" }, { value: "transient", label: "Transient" }]} />
      {r.mode === "transient" ? (
        <div className="grid2">
          <Field label="End time"><NumberInput value={r.end_time} unit="s" min={1e-12} onChange={(end_time) => patch("run", { end_time })} /></Field>
          <Field label="Stored frames" hint="Animation frames in Results"><NumberInput value={r.snapshot_count} integer min={1} max={200} onChange={(snapshot_count) => patch("run", { snapshot_count })} /></Field>
        </div>
      ) : (
        <Field label="Convergence tolerance" hint="Residual = relative change of the solution per flow-through time."><NumberInput value={r.tolerance} min={1e-14} max={0.5} onChange={(tolerance) => patch("run", { tolerance })} /></Field>
      )}
      {r.mode === "transient" && stats?.estimated_steps && <div className="muted small">≈ {formatNumber(stats.estimated_steps, 3)} steps, ~{formatNumber(stats.estimated_wall_seconds, 3)} s wall time.</div>}
      <div className="grid2">
        <Field label="CFL number" hint="Smaller = more stable, slower"><NumberInput value={r.cfl} min={0.01} max={0.9} onChange={(cfl) => patch("run", { cfl })} /></Field>
        <Field label="Max iterations"><NumberInput value={r.max_iterations} integer min={1} onChange={(max_iterations) => patch("run", { max_iterations })} /></Field>
        <Field label="Monitor interval" hint="Record every N iterations"><NumberInput value={r.monitor_interval} integer min={1} onChange={(monitor_interval) => patch("run", { monitor_interval })} /></Field>
      </div>
      <h3>Advanced</h3>
      <div className="grid2">
        <Field label="Fixed time step" hint="Empty = automatic (CFL)"><NumberInput value={r.fixed_dt} unit="s" allowEmpty placeholder="auto" onEmpty={() => patch("run", { fixed_dt: null })} onChange={(fixed_dt) => patch("run", { fixed_dt })} /></Field>
        <Field label="Convection blend" hint="0 central … 1 upwind. Empty = adaptive"><NumberInput value={r.convection_blend} allowEmpty min={0} max={1} placeholder="adaptive" onEmpty={() => patch("run", { convection_blend: null })} onChange={(convection_blend) => patch("run", { convection_blend })} /></Field>
      </div>
    </div>
  );
}

function Monitors({ config, setConfig, sel, setSel }: { config: CaseConfig; setConfig: SetConfig; sel: Selection; setSel: (s: Selection) => void }) {
  const up = (id: string, v: Partial<Probe>) => setConfig((c) => ({ ...c, probes: c.probes.map((p) => (p.id === id ? { ...p, ...v } : p)) }));
  return (
    <div className="stack">
      <div><h2>Monitors</h2><p className="lead">Point probes record velocity, pressure{config.models.energy ? " and temperature" : ""} every few iterations. Forces{config.models.energy ? " and heat-transfer rates" : ""} on every obstacle are always recorded.</p></div>
      <div className="row between"><h3>Probes</h3>
        <button className="btn sm" onClick={() => setConfig((c) => {
          const id = nextId("probe", c.probes);
          setSel({ kind: "probe", id });
          return { ...c, probes: [...c.probes, { id, name: `Probe ${c.probes.length + 1}`, x: c.domain.width / 2, y: c.domain.height / 2, z: null }] };
        })}>＋ Add probe</button></div>
      {config.probes.length === 0 && <div className="muted small">No probes yet.</div>}
      {config.probes.map((p) => (
        <Card key={p.id} sel={sel?.kind === "probe" && sel.id === p.id} onClick={() => setSel({ kind: "probe", id: p.id })}>
          <header><input type="text" value={p.name} onChange={(e) => up(p.id, { name: e.target.value })} />
            <button className="btn sm ghost danger" onClick={() => setConfig((c) => ({ ...c, probes: c.probes.filter((x) => x.id !== p.id) }))}>Remove</button></header>
          <div className={config.dimension === "3d" ? "grid3" : "grid2"}>
            <Field label="x"><NumberInput value={p.x} unit="m" onChange={(x) => up(p.id, { x })} /></Field>
            <Field label="y"><NumberInput value={p.y} unit="m" onChange={(y) => up(p.id, { y })} /></Field>
            {config.dimension === "3d" && <Field label="z"><NumberInput value={p.z} unit="m" allowEmpty placeholder="mid" onEmpty={() => up(p.id, { z: null })} onChange={(z) => up(p.id, { z })} /></Field>}
          </div>
        </Card>
      ))}
    </div>
  );
}

function SolverSection({ config, patch, projectId }: { config: CaseConfig; patch: Patch; projectId: string }) {
  const [solvers, setSolvers] = useState<SolverInfo[] | null>(null);
  useEffect(() => { api.solvers().then(setSolvers).catch(() => setSolvers([])); }, []);
  const s = config.solver;
  const all = solvers ?? [];
  const current = all.find((x) => x.id === s.backend);
  const fallback = current?.fallback ? all.find((x) => x.id === current.fallback) : undefined;
  const willUse = current && !current.available.ok && s.allow_fallback ? fallback : current;
  return (
    <div className="subcard stack" style={{ gap: 10 }}>
      <Field label="Solver" hint={current?.description}>
        <select value={s.backend} onChange={(e) => patch("solver", { backend: e.target.value as BackendId })}>
          {(all.length ? all.filter((x) => x.selectable) : [{ id: "native", label: "Gearhead CFD native (finite volume)", available: { ok: true, mode: "python", detail: "" } } as SolverInfo]).map((x) => (
            <option key={x.id} value={x.id}>{x.label}{x.available.ok ? "" : " — not installed"}</option>
          ))}
        </select>
      </Field>
      {current && current.id !== "native" && (
        <div className={`banner small ${current.available.ok ? "" : "warn"}`}>
          {current.available.ok
            ? <>Available via <b>{current.available.mode}</b> ({current.available.detail}).</>
            : <>Not available on this server ({current.available.detail}). {s.allow_fallback && fallback ? <>Runs will use <b>{fallback.label}</b> instead.</> : "Runs are blocked until it is installed."}</>}
        </div>
      )}
      {current && current.id !== "native" && (
        <Toggle checked={s.allow_fallback} onChange={(allow_fallback) => patch("solver", { allow_fallback })} label="Use the built-in Python equivalent when the tool is missing"
          hint={fallback ? `Fallback: ${fallback.label}` : undefined} />
      )}
      {(s.backend === "pyfr" || s.backend === "fenicsx") && (
        <Field label="Polynomial order" hint={s.backend === "pyfr" ? "PyFR solution points per direction = order + 1" : "Velocity degree of the Taylor–Hood pair (pressure = order − 1)"}>
          <NumberInput value={s.order} integer min={s.backend === "fenicsx" ? 2 : 1} max={6} onChange={(order) => patch("solver", { order })} />
        </Field>
      )}
      {s.backend === "openfoam" && (
        <Field label="MPI processes" hint="> 1 runs decomposePar + mpirun -parallel + reconstructPar">
          <NumberInput value={s.processes} integer min={1} max={256} onChange={(processes) => patch("solver", { processes })} />
        </Field>
      )}
      {willUse && willUse.capabilities.turbulence === false && config.models.turbulence !== "laminar" && (
        <div className="banner err small">{willUse.label} is laminar — RANS turbulence needs OpenFOAM.</div>
      )}
      {current?.exportable && (
        <div className="row wrap">
          <a className="btn sm" href={api.exportCaseUrl(projectId, current.id)}>⬇ Download {current.label} case files</a>
          <span className="muted small">Saved settings · runnable outside Gearhead CFD</span>
        </div>
      )}
    </div>
  );
}
