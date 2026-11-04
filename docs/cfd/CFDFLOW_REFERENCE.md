# CFDFlow — a BaramFlow-style CFD workbench for the web

CFDFlow is a full-stack web application for setting up, running and post-processing **2-D and 3-D**
computational-fluid-dynamics simulations entirely in the browser. It follows the workflow of
[BaramFlow](https://baramcfd.org/en/manual-en/manual-baramflow-en/introduction-en/) — *General → Models →
Materials → Boundary conditions → Initialization → Run conditions → Monitor → Run → Graphics/Report*.

Every case can be solved by one of four **solver backends**, chosen per project:

| Backend | What it is | When the tool is not installed |
|---|---|---|
| **Native** (default) | Built-in NumPy/SciPy finite-volume solver, 2-D and 3-D | always available |
| **OpenFOAM** | CFDFlow writes a complete OpenFOAM case (blockMesh + topoSet/subsetMesh, simpleFoam / pimpleFoam / buoyantBoussinesq*Foam, laminar or **k-ε / k-ω SST**), runs it natively, in Docker or WSL, and reads the result back | falls back to the native solver (same equations, written in Python) |
| **FEniCSx** | A standalone DOLFINx program (Taylor–Hood Q2/Q1 finite elements, monolithic Newton/MUMPS) shipped with CFDFlow and run in a DOLFINx environment | falls back to a pure-Python **stabilised finite-element solver** |
| **PyFR** | CFDFlow writes a Gmsh mesh + PyFR `.ini` for the artificial-compressibility `ac-navier-stokes` system (high-order flux reconstruction) and runs PyFR | falls back to a pure-Python **artificial-compressibility solver** |

The whole stack still installs with `pip` / `npm` / `docker compose`; the external solvers are optional and are found
automatically (on `PATH`, as Docker images, or in WSL). See [Solver backends](#solver-backends-openfoam-fenicsx-pyfr).

| Layer | Technology |
|---|---|
| Frontend | TypeScript, React 19, Vite, React Router (no UI/chart libraries — canvas + SVG written for this project). Branded **Gearhead CFD**: the Gearhead FEA (`../webfea`) design tokens (vellum light / blueprint dark theme, stress-red accent, contour-ramp colours), its fonts (Space Grotesk, Hanken Grotesk, JetBrains Mono) and hex-nut logo, with "CFD" in place of "FEA" |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2, Pydantic 2, WebSockets |
| Database | PostgreSQL 16 (JSONB for case definitions and monitor data) — SQLite is used for tests |
| Solvers | NumPy + SciPy finite-volume Navier–Stokes / energy solver, 2-D and 3-D (written for this project); OpenFOAM, FEniCSx (DOLFINx) and PyFR adapters with pure-Python fallbacks; `foamlib` (OpenFOAM file reader), `meshio` (ParaView export) |
| Packaging | Docker (multi-stage), docker-compose (+ an overlay that lets the app start solver containers), Makefile, a PyFR Dockerfile |

> **Scope, stated plainly.** Incompressible, single-phase, Newtonian flow with constant properties, optional energy
> equation and Boussinesq buoyancy, in 2-D or 3-D on uniform Cartesian meshes with staircase obstacles. Turbulence
> (RANS k-ε / k-ω SST) is available through the OpenFOAM backend only. Compressible, multiphase, body-fitted or imported
> CAD geometry are out of scope (see [Limitations](#limitations-and-roadmap)). Accuracy is verified for every backend
> (see [Validation](#validation)).

---

## Contents

1. [Quick start](#quick-start)
2. [Features](#features)
3. [How it maps to BaramFlow](#how-it-maps-to-baramflow)
4. [Architecture](#architecture)
5. [Why this solver stack](#why-this-solver-stack)
6. [The solver](#the-solver)
7. [3-D](#3-d)
8. [Solver backends: OpenFOAM, FEniCSx, PyFR](#solver-backends-openfoam-fenicsx-pyfr)
9. [Validation](#validation)
10. [Project layout](#project-layout)
11. [Configuration](#configuration)
12. [REST / WebSocket API](#rest--websocket-api)
13. [The case definition](#the-case-definition)
14. [Testing](#testing)
15. [Limitations and roadmap](#limitations-and-roadmap)
16. [Work log — what was built and verified](#work-log--what-was-built-and-verified)

---

## Quick start

### Option A — Docker (everything, one command)

```bash
cp .env.example .env          # optional; change POSTGRES_PASSWORD for anything but local use
docker compose up --build
# open http://localhost:8000
```

This starts PostgreSQL 16 and one `app` container that serves the API, the solver workers and the built UI.
Result files are stored in the `rundata` volume, the database in `pgdata`.

### Option B — local development

Prerequisites: Python ≥ 3.11, Node ≥ 20, a PostgreSQL 16 server (or `make db` to start one in Docker).

```bash
# 1. database (skip if you already have one)
make db

# 2. backend  (http://localhost:8000, docs at /docs)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # adjust CFD_DATABASE_URL if needed
python -m uvicorn app.main:app --reload --port 8000

# 3. frontend (http://localhost:5173, proxies /api and WebSockets to :8000)
cd frontend
npm install
npm run dev
```

No PostgreSQL at hand? For a quick look you can use SQLite:
`CFD_DATABASE_URL=sqlite:///./cfdflow.db python -m uvicorn app.main:app --port 8000`.

### First five minutes

1. Open the UI and click **Lid-driven cavity** under *Examples*, name the project, **Create**.
2. In **① Setup** click through the sections; the preview on the right updates live and the *Case check* panel
   explains anything that is wrong. Try dragging the probe.
3. In **② Run & monitor** press **Start new run** — residuals, monitors and a live flow-field preview stream in.
4. When it finishes, **View results**: switch fields, add streamlines/vectors, draw a *Line probe*, export to ParaView.

Then try *Cylinder in channel* (transient vortex street with animation), *Natural convection cavity* and
*Electronics cooling* (heated chips, heat-transfer rates).

**3-D:** open *Flow past a sphere (3-D)* or *Lid-driven cube (3-D)*, run it, and in **Results** pick the slice plane
(x–y / x–z / y–z) and drag the position slider. Any case can be switched between 2-D and 3-D in **Setup → General**.

**External solvers:** in **Setup → General → Solver** choose OpenFOAM, FEniCSx or PyFR. The panel says whether the tool
was found and which Python equivalent runs otherwise, and offers the generated case files as a download.
To make all three available through Docker (≈ 6 GB):

```bash
make solver-images      # docker pull opencfd/openfoam-default:2406 dolfinx/dolfinx:v0.9.0 ; docker build docker/pyfr
```

---

## Features

**3-D and solver backends**
- **2-D / 3-D switch** per case: domain depth, `nz`, front/back boundaries, z-position and z-extent of obstacles
  (box, cylinder along z, **sphere**), heat sources and probes, gravity z, initial w.
- **Native 3-D solver** (finite volume, MAC grid, projection method; FFT-preconditioned CG pressure solver).
- **OpenFOAM backend**: full case generation, live residuals / probes / forces while it runs, graceful stop
  (`stopAt writeNow`), MPI parallel runs, **RANS turbulence (k-ε, k-ω SST)**, results read back into the viewer.
- **FEniCSx backend** (DOLFINx finite elements) and **PyFR backend** (high-order flux reconstruction).
- **Pure-Python fallbacks** for all three: native solver for OpenFOAM, stabilised FEM for FEniCSx,
  artificial compressibility for PyFR. The case check says which one will run.
- **Download the input deck** (OpenFOAM case with `Allrun`, DOLFINx script + `case.json`, PyFR mesh + `.ini` +
  run script) to run it on a cluster.
- **3-D results**: slices in any of the three planes at any position, with vectors, streamlines, line probes and hover read-out
  in plane coordinates; 3-D obstacle loads (Fx, Fy, Fz in N, Q in W); ParaView export of the full hexahedral mesh.

**Case setup (guided, BaramFlow-style left-hand navigation)**
- Sections: General · Geometry · Mesh · Models · Materials · Boundary conditions · Initialization · Run conditions · Monitors.
- **Interactive geometry canvas**: domain, obstacles (rectangles / circles), heat sources and probes are drawn live over
  the real mesh; boundary types are colour-coded with flow arrows; obstacles, sources and probes can be **dragged**.
- **Live case check** (debounced, server-side): errors block the run (e.g. inlet without outlet, no fluid cells), warnings explain
  physics and numerics (Reynolds number > 2300 → laminar solver caveat, coarse-mesh cell-Reynolds number, unresolved
  obstacles, unstable fixed time step, Rayleigh number, estimated cost…). Section badges in the navigation show counts.
- **Autosave** with status indicator; unsaved edits are flushed when you leave the project; server-side validation errors are
  shown in plain language and never discard your input.
- Number fields accept scientific notation, have units, clamp to valid ranges and flag invalid input.
- Material presets (air, water, oil, non-dimensional) with derived ν, α and Pr.
- Seven built-in examples with published benchmark data; duplicate / delete projects.

**Running**
- Each run is an isolated **solver process** with a frozen copy of the case (later edits never change a run in progress).
- Bounded concurrency queue (`CFD_MAX_CONCURRENT_RUNS`), **Stop** at any time (a partial result is kept), crash and
  server-restart recovery.
- **Live monitoring over WebSocket**: progress bar, residual history (log scale), probe / force / heat-transfer histories,
  console log, and a **live flow-field preview** refreshed every ~2.5 s.

**Results**
- Fields: velocity magnitude, u, v, pressure, temperature, vorticity; four colour maps (viridis, inferno, turbo,
  coolwarm, gray), automatic or locked/manual colour range, smooth or per-cell shading.
- Overlays: **velocity vectors**, **streamlines** (evenly spaced, recomputed per frame), hover read-out of every field.
- **Transient animation**: frame slider, play/pause, speed control, frame prefetch.
- **Line probe**: drag a line across the field → profile chart (any field) + CSV download.
- Probe, force and heat-transfer time histories; run summary (iterations, wall time, Δp, T range, mass imbalance);
  **loads on each obstacle** (Fx, Fy per metre depth, heat-transfer rate Q).
- Export: **ParaView `.vtu`**, field data `.csv`, monitor history `.csv`, `.png` of the current view.
- Light / dark theme.

---

## How it maps to BaramFlow

| BaramFlow (OpenFOAM-based desktop app) | CFDFlow |
|---|---|
| Project folder (`case/`, `configuration.h5`) | PostgreSQL `projects` row (JSONB case) + `data/runs/<id>/` result files |
| baramMesh (blockMesh / snappyHexMesh) | Uniform Cartesian mesh with cell-masked obstacles (geometry canvas); with the OpenFOAM backend: blockMesh + topoSet/subsetMesh |
| General / Models (energy, turbulence) | General (2-D / 3-D, solver backend), Models (energy, buoyancy; turbulence laminar / k-ε / k-ω SST via OpenFOAM) |
| Materials | Materials (presets + custom, constant properties) |
| Boundary Conditions (inlet / outlet / wall / symmetry…) | Boundary conditions per side: velocity inlet (uniform / parabolic), pressure outlet, moving/fixed-T wall, symmetry |
| Cell zone conditions (sources, porous, MRF…) | Volumetric heat sources (others on the roadmap) |
| Initialization, Run Conditions | Initialization, Run conditions (steady / transient, CFL, tolerance, …) |
| Monitor (force, point values) | Monitors: point probes, obstacle forces, heat-transfer rates, residuals |
| Run (OpenFOAM solver in a subprocess, live residual plot) | Run & monitor (solver in a subprocess — native, or OpenFOAM / DOLFINx / PyFR launched by it — WebSocket streaming) |
| Graphics (contour, vector, streamline), ParaView | Results (contour, vectors, streamlines, line probe, animation), `.vtu` export for ParaView |

---

## Architecture

```
┌────────────────────────── Browser (React + TypeScript) ──────────────────────────┐
│  Projects │ Setup (forms + geometry canvas + case check) │ Run & monitor │ Results │
└───────┬───────────────────────────────┬──────────────────────────────┬───────────┘
        │ REST (JSON)                   │ WebSocket /api/runs/{id}/stream│ REST (fields as base64 float32)
┌───────▼───────────────────────────────▼──────────────────────────────▼───────────┐
│ FastAPI app (uvicorn)                                                            │
│  api/projects.py  api/runs.py  services/checks.py  services/run_manager.py       │
│  ┌──────────────────────────────────────┐                                        │
│  │ RunManager (scheduler thread)        │── spawns ──►  python -m app.worker <id> │
│  │  queue · concurrency · reaping       │              (one OS process per run)   │
│  └──────────────────────────────────────┘                    │                    │
└───────────────┬─────────────────────────────────────────────┼────────────────────┘
                │ SQLAlchemy                                  │ solver/backends: resolve(case)
        ┌───────▼────────┐                       ┌────────────▼───────────────────────────────┐
        │ PostgreSQL     │◄──────────────────────│ native  → ns2d.py / ns3d.py (in process)   │
        │ projects, runs,│  worker writes status │ openfoam→ case files → Allrun ─┐           │
        │ monitor_samples│  progress, monitors,  │ fenicsx → run_fenicsx.py ──────┤ launcher: │
        │ run_logs       │  logs                 │ pyfr    → mesh.msh + .ini ─────┘ native /  │
        └────────────────┘                       │           docker / wsl, log parsed live    │
                                                 │ fallbacks: native, python-fem, python-ac   │
                                                 └────────────┬───────────────────────────────┘
                                                              │ result sampled onto the CFDFlow grid
                                                              ▼
                                         data/runs/<id>/ latest.npz, result.npz (+ openfoam/, fenicsx/, pyfr/)
```

The **`SolverBackend` interface** (`backend/app/solver/backends/base.py`) has four methods: `availability()`,
`check(case)`, `run(case, hooks, run_dir)` and `export_case(case, dir)`. Every backend returns its result on the
structured CFDFlow grid in one cell-centred format, so the viewer, probes, exports and summaries are the same whatever
produced the numbers.

Key design decisions

- **Process per run, state in PostgreSQL.** The API never runs numerics, so it stays responsive; a runaway or crashing
  solver cannot take the API down; runs can be cancelled cleanly (cooperative flag, hard-kill after a grace period).
  The scheduler also marks runs orphaned by a server restart as failed.
- **Immutable run snapshots.** A run stores its own copy of the case JSON, so results always correspond to the exact
  inputs that produced them.
- **Efficient field transport.** Fields travel as little-endian `float32` base64 blobs (≈ 6 fields × 4 B × cells) instead of
  JSON number arrays; the browser decodes into `Float32Array`s and renders with `ImageData`.
- **Atomic result files.** Live snapshots are written to a temp file and renamed, so the viewer never reads a half-written file.
- **Same rasterisation in both places.** The geometry canvas rasterises obstacles in TypeScript with exactly the rule used
  by the solver (cell centre inside the shape), so what you see is the mesh the solver uses.

---

## Why this solver stack

The brief was to find solver/mesh packages that are written in Python or integrate easily with a Python backend.
Candidates that were considered:

| Candidate | Verdict |
|---|---|
| **OpenFOAM** (what BaramFlow wraps) | Not Python; needs a large native install and file-based case management. Out of scope for a `pip`-installable web app (a `SolverBackend` seam is the place to add it, see roadmap). |
| **FEniCSx / Oasis** | Python API, excellent, but depends on compiled DOLFINx/PETSc/UFL stacks that are painful to install in a slim container and awkward to run per-request. |
| **PyFR** | Python, high-order flux reconstruction, but targets GPU/OpenMP backends and compressible/high-order work; heavy for an interactive incompressible tool. |
| **SU2** | C++ with Python wrappers; large build. |
| **Gmsh (`pip install gmsh`)** | Installs cleanly and is the natural unstructured mesher for a future 3-D/complex-geometry mode. |
| **meshio** | Pure Python mesh I/O — **used** for the ParaView `.vtu` export. |
| **NumPy / SciPy** | Pure `pip`; sparse LU (`splu`) is more than adequate for 2-D pressure Poisson systems — **used** for the solver. |

Original decision: a purpose-written **finite-volume solver on NumPy/SciPy**. It installs everywhere, runs in a plain Python
worker process, is easy to verify against textbook benchmarks, and still handles problems of up to a few hundred
thousand cells at interactive speed (see [timings](#validation)).

**Update — OpenFOAM, FEniCSx and PyFR are now integrated** behind the `SolverBackend` seam, without making any of them a
hard dependency:

| Tool | Python package available? | What CFDFlow does |
|---|---|---|
| OpenFOAM | No Python API. [`foamlib`](https://github.com/gerlero/foamlib) reads and writes OpenFOAM files | **Uses `foamlib`** to read result fields. Case generation (`openfoam.py`) is written for CFDFlow, and the generated dictionaries are readable OpenFOAM input. Runs the real OpenFOAM when present. **Python equivalent:** the native 2-D/3-D solver (`ns3d.py`) — same laminar incompressible / Boussinesq equations as icoFoam / pimpleFoam / buoyantBoussinesqPimpleFoam |
| FEniCSx | `dolfinx` (conda / Docker only — no Windows or plain-pip wheels) | Ships a standalone DOLFINx program (`fenicsx_script.py`) and runs it wherever DOLFINx exists. **Python equivalent:** `fem_fallback.py`, a NumPy/SciPy finite-element Navier–Stokes solver (Q1/Q1 PSPG/SUPG) |
| PyFR | `pyfr` on pip, but its compute backends need a C compiler + libxsmm (OpenMP) or CUDA/HIP — unusable on stock Windows | Writes the mesh (`.msh`) and `.ini`, runs PyFR, and reads its VTU output with a small reader written for CFDFlow (PyFR writes VTK 2.1, which meshio rejects). A Dockerfile builds a working PyFR. **Python equivalent:** `ac_fallback.py`, the same artificial-compressibility equations and dual time stepping as PyFR's `ac-navier-stokes` system, on 2nd-order finite volumes |

---

## The solver

`backend/app/solver/ns2d.py`

Governing equations (2-D, incompressible, Newtonian, constant properties, depth = 1 m):

```
∇·u = 0
∂u/∂t + u·∇u = −∇p/ρ + ν∇²u − g β (T − T_ref)          (Boussinesq term optional)
∂T/∂t + u·∇T  = α∇²T + q'''/(ρ cp)                       (energy equation optional)
```

Method

- **Grid**: uniform Cartesian *staggered* (MAC) grid — `u` on vertical faces, `v` on horizontal faces, `p`, `T` at cell centres.
  Obstacles are cell masks (staircase); no-slip is imposed by mirror ghost values so the wall sits on the cell face.
- **Time integration**: Chorin fractional-step projection. Explicit momentum predictor with
  - convection: Hirt/Griebel **blended central / donor-cell** scheme (`γ = min(1.2·max(Courant), 1)` by default, user-settable),
  - diffusion: second-order central,
  - then the pressure Poisson equation `∇²p = ∇·u*/Δt` solved with a **pre-factorised sparse LU** (`scipy.sparse.linalg.splu`),
    factorised once per run because the mesh and boundary conditions are static, and the velocity correction.
- **Pressure BCs**: Neumann on walls/inlets/obstacles, Dirichlet `p = 0` (gauge) at outlets with the half-cell distance
  respected; each fluid region without an outlet is pinned at one cell, so closed cavities and enclosed pockets are well-posed.
- **Time step**: `Δt = CFL · min(1/(2ν(1/Δx²+1/Δy²)), Δx/|u|max, Δy/|v|max, buoyancy limit)` with ν replaced by max(ν, α) when
  heat transfer is on.
- **Boundary conditions**: wall (stationary or moving, adiabatic or fixed T), velocity inlet (uniform, or parabolic with
  unit mean computed over the *open* faces so steps work), pressure outlet (zero-gradient `u` and `T`), symmetry.
- **Energy**: cell-centred finite volume with the same blended convection, Dirichlet interfaces on fixed-temperature
  walls/obstacles using the half-cell distance, volumetric sources, Boussinesq coupling into momentum.
- **Steady problems** march in pseudo-time until the residual
  `‖Δφ‖₂/‖φ‖₂ · (L/U)/Δt` (relative change of the solution per flow-through time) drops below the tolerance.
- **Decoupled energy shortcut**: for steady problems *without* buoyancy the flow is converged first, then the temperature is
  obtained with **one direct sparse solve** (hybrid differencing: central for cell Péclet < 2, blending to upwind above).
  This cut the electronics-cooling example from ~200 s to ~45 s and the thermal solve itself takes ~40 ms.
- **Diagnostics recorded**: residuals (u, v, p, T), probe values, max speed, dt, mass imbalance, and for each obstacle
  the pressure + viscous force (staircase surface) and the heat-transfer rate.

Cost model (single core, measured): ≈ `1.5 ms + 0.45 µs × cells` per step — e.g. 64×64 cavity to convergence in ~16 s,
160×40 duct in ~40 s, 220×41 vortex shedding to t = 15 s in ~25 s.

---

## 3-D

`backend/app/solver/ns3d.py` (`FlowSolver3D`) + `grid3d.py`

The native 3-D solver uses the same method as the 2-D solver, written once for any number of axes. In 2-D mode it
reproduces `ns2d.py` to machine precision (this is tested).

- **Grid**: `nx × ny × nz` MAC grid. `u, v, w` live on the faces normal to x, y, z; `p` and `T` live at cell centres. Sides:
  `left/right` (x), `bottom/top` (y), `back/front` (z = 0 / z = depth). Moving walls slide along +x (bottom, top, back,
  front) or +y (left, right). Parabolic inlets use `36 s(1−s) t(1−t)` over the two tangential directions (mean = inlet speed).
- **Obstacles**: `rectangle` = box (`width × height × depth`), `circle` = cylinder whose axis is parallel to z, `sphere`. `z = null`
  means mid-depth and `depth = null` means full depth, so a 2-D case switched to 3-D is its extrusion.
- **Pressure**: up to 15 000 fluid cells the system is LU-factorised once. Above that, **preconditioned CG** is used. The preconditioner is the
  exact FFT inverse (DCT-II / DST-II / DCT-IV / DST-IV per axis, depending on wall/outlet ends) of the obstacle-free
  Laplacian. With a warm start this takes 3–20 iterations per step and was faster than LU even at 32³ (38 vs 60 ms/step).
  Closed fluid pockets are pinned through a phantom Dirichlet face, which keeps the matrix symmetric and is exact for a
  compatible right-hand side.
- **Energy, buoyancy, heat sources, probes, loads**: as in 2-D. Loads are in N and W (2-D: per metre of depth).
  Vorticity in 3-D is |ω|; on a slice the viewer shows the vorticity component normal to the slice.
- **Results**: one cell-centred file (`u, v, w, p, T` of shape `nx × ny × nz` per frame). `GET /fields?axis=&index=`
  returns any slice in the 2-D payload format, so the existing canvas, streamlines, vectors and line probe work unchanged.
  `.vtu` export writes the full hexahedral mesh, and CSV has one row per cell with x, y, z.
- **Cost** (single core): ≈ `2 ms + 2 µs × cells` per step. Measured on the 3-D examples: square duct (7 200 cells) 1 s, sphere
  in a duct (19 440 cells) 10 s, lid-driven cube 24³ 30 s, heated chip (10 000 cells, energy) 29 s.

---

## Solver backends: OpenFOAM, FEniCSx, PyFR

Selected in **Setup → General → Solver** (`case.solver.backend`). The worker calls `resolve(case)`:

1. If the backend's tool is available it is used.
2. Otherwise, with `allow_fallback` (the default), the equivalent built-in Python solver runs. The run log and summary
   say so: `requested_backend` and `backend` are both recorded.
3. Otherwise the case check reports an error and the run is refused.

**Finding the tools** (`backends/launcher.py`): `CFD_<TOOL>_LAUNCHER=auto` tries, in order, the native executable on `PATH`
(`simpleFoam`, a Python that can `import dolfinx`, `pyfr`), a local Docker image (`docker image inspect`), and WSL. Docker
runs bind-mount the run directory at `/case`. The tools' output is streamed and parsed live into the same residual/monitor
charts as native runs. **Stop** works for all of them: OpenFOAM gets `stopAt writeNow` and keeps a partial result,
DOLFINx watches for a `STOP` file, and PyFR is killed and then exported.

### OpenFOAM (`backends/openfoam.py`)

| CFDFlow | Generated OpenFOAM case |
|---|---|
| Domain + mesh | `blockMeshDict`: one `hex` block `nx × ny × nz`; 2-D = one cell thick with `empty` front/back |
| Obstacles | per obstacle `topoSetDict.obs_<id>` (`boxToCell` / `cylinderToCell` / `sphereToCell` + `invert`) and `subsetMesh -overwrite -patch obs_<id>` → body-surface wall patch |
| Isothermal | `simpleFoam` (SIMPLEC) / `pimpleFoam` |
| Energy, no buoyancy | the same + `scalarTransport` function object for T (passive scalar with constant properties, as in the native solver) |
| Energy + buoyancy | `buoyantBoussinesqSimpleFoam` / `buoyantBoussinesqPimpleFoam` (`CFD_OPENFOAM_BUOYANT_SOLVER=compressible` → `buoyantSimpleFoam` / `buoyantPimpleFoam` with `heRhoThermo` + Boussinesq EoS) |
| Turbulence | `kEpsilon` / `kOmegaSST` with wall functions; inlet k, ε, ω from 5 % intensity and a mixing length of 0.07 D_h |
| Boundary conditions | walls `noSlip` / moving `fixedValue`; inlets `fixedValue` or parabolic `exprFixedValue`; outlets `p = 0`, zero-gradient U, `inletOutlet` T; symmetry; fixed-T / adiabatic walls and bodies |
| Heat sources | `cellSet` + `scalarSemiImplicitSource` (K/s on T, or W/m³ on h) |
| Monitors | `probes`, `forces` per obstacle (`wallHeatFlux` for the compressible form; otherwise Q from the surface temperature gradient) |
| Run | `Allrun`: blockMesh → topoSet/subsetMesh → checkMesh → writeCellCentres → solver (`decomposePar` + `mpirun -parallel` + `reconstructPar` when *MPI processes* > 1) |
| Result | time directories read with **foamlib**, mapped onto the CFDFlow grid through the cell centres (the meshes are identical: same "cell centre inside the shape" rule) |

The case targets the ESI/OpenCFD distribution (openfoam.com, tested with **v2406**, Docker image
`opencfd/openfoam-default:2406`). The downloaded zip contains `Allrun`, so it runs as-is in any OpenFOAM v2306+ shell.

Two findings from testing that shaped the design:

- Plain-SIMPLE `buoyantSimpleFoam` stalled (p_rgh residual stuck at 0.08) on the forced-convection chip case. SIMPLEC `simpleFoam`
  + `scalarTransport` converges in 730 iterations, so forced convection uses that path.
- `buoyantSimpleFoam` with the Boussinesq *equation of state* keeps the density in every term. It is not the Boussinesq
  *approximation*, and on the non-dimensional cavity (β·ΔT = 1) it gives Nu = 6.0 instead of 4.5. With real air
  (β·ΔT = 0.03) it gives 4.78. The true-Boussinesq solvers give 4.65 on the non-dimensional case, so they are the default.

### FEniCSx (`backends/fenicsx.py`, `fenicsx_script.py`)

A self-contained DOLFINx v0.9 program, copied into the run directory with `case.json`:

- structured hex/quad mesh of the box; obstacle cells are removed with `create_submesh`; exterior facets are tagged by side or obstacle;
- Taylor–Hood **Q2/Q1** for (u, p), plus Q1 temperature, as **one monolithic Newton solve** (PETSc, MUMPS LU) of
  Navier–Stokes + Boussinesq + energy;
- steady: direct Newton, with viscosity continuation 8 → 4 → 2 → 1 × ν if it fails. Transient: backward Euler;
- outlets use the natural "do-nothing" condition; closed domains pin p at one vertex;
- output: u, v, w, p, T evaluated at the CFDFlow cell centres (`result_fenicsx.npz`); progress is printed as `CFDFLOW {json}` lines.

**Fallback — `fem_fallback.py`** (pure NumPy/SciPy): Q1/Q1 elements with PSPG + SUPG stabilisation (Tezduyar τ), a monolithic
velocity–pressure system, Picard iterations (steady) or backward Euler (transient), and a SUPG temperature equation.
The geometry is identical for every element, so reference matrices are built once and the velocity-dependent parts are
assembled with vectorised `einsum`. A banded LU ordering (longest axis slowest) is 3× faster than COLAMD in 3-D.
Limit: `CFD_MAX_FEM_CELLS` (40 000). Equal-order stabilisation is diffusive on coarse meshes: the cavity centre velocity
is −0.154 / −0.181 / −0.195 on 20² / 40² / 80², converging to Ghia's −0.206.

### PyFR (`backends/pyfr.py`)

- `mesh.msh`: the fluid cells as Gmsh 2.2 quads/hexes, written by CFDFlow (no Gmsh needed). Physical groups are `fluid`,
  one per side, and one per obstacle.
- `config.ini`: `system = ac-navier-stokes`, order `p` (Setup → *Polynomial order*), dual time stepping (backward Euler,
  RK45 pseudo-steps with a local-PI controller, **p-multigrid** V-cycle), Rusanov/LDG fluxes, `ac-in-fv` (uniform or
  parabolic expression), `ac-out-fp`, `no-slp-wall` (moving walls via u/v/w), `slp-wall`, and the `fluidforce` and
  `pseudostats` plugins. `ac-zeta = 2.5 U²`; `dt` is snapped so the output interval is an exact multiple (PyFR requires it).
- Steady cases are integrated in time to 20 flow-through times, because PyFR's AC system has no steady mode.
- `run_pyfr.sh` runs `pyfr import`, `pyfr run -b openmp` (or `CFD_PYFR_DEVICE=cuda|hip|…`) and `pyfr export`. CFDFlow averages the
  high-order VTU output onto its cells.
- **Isothermal only** (PyFR's AC system has no energy equation); the case check says so.
- `docker/pyfr/Dockerfile` builds a working image: PyFR 2.0.3 + libxsmm (`STATIC=0 BLAS=0`, `main`; the older pinned commit
  segfaults in `libxsmm_fsspmdm_create` with PyFR 2.x) + GCC + OpenMPI. Build it with `make solver-images`.

**Fallback — `ac_fallback.py`**: the same artificial-compressibility system (`∂p/∂τ + ζ∇·u = 0`), SSP-RK3 pseudo-time
stepping, dual time stepping for transient runs, on the MAC finite-volume operators of `ns3d.py`; outlet `p = 0` is
imposed by relaxing the boundary-cell pressure at the acoustic rate.

---

## Validation

All numbers below were produced by this code (the first four are automated tests in `backend/tests/test_solver.py`).

| Case | Quantity | Reference | CFDFlow | Mesh |
|---|---|---|---|---|
| Lid-driven cavity, Re = 100 (Ghia, Ghia & Shin 1982) | u(0.5, 0.9766) | 0.84123 | 0.8416 | 64² |
| | u(0.5, 0.5) | −0.20581 | −0.2051 | 64² |
| | u(0.5, 0.2813) | −0.15662 | −0.1550 | 64² |
| | u(0.5, 0.0547) | −0.03717 | −0.0372 | 64² |
| Plane Poiseuille channel (analytic) | Δp = 12 μ U L / H² | 4.680 | 4.594 (−1.8 %) | 80×20 |
| | centre-line u / U | 1.500 | 1.493 | 80×20 |
| Differentially heated cavity, Ra = 10⁴, Pr = 0.71 (de Vahl Davis 1983) | Nu (hot wall) | 2.243 | 2.269 (+1.2 %) | 32² |
| | u_max (dimensionless 16.18) | 0.192 | 0.192 | 32² |
| Same, Ra = 10⁵ | Nu (hot wall) | 4.519 | 4.60 (+1.8 %) | 48² |
| Heated block in a duct | heat leaving block vs. enthalpy flux at outlet | equal | 23.67 vs 23.67 W/m | 160×40 |
| Mass conservation (closed / open domains) | net boundary flux | 0 | ≤ 1e-15 | — |

**3-D native solver**

| Case | Quantity | Reference | CFDFlow | Mesh |
|---|---|---|---|---|
| 2-D cavity extruded between symmetry planes | u field vs. 2-D solver | identical | max diff < 1e-10, w ≡ 0 | 16²×4 |
| Same solver in 2-D mode vs. `ns2d.py` | u, v, p after 20 steps | identical | max diff < 1e-13 | 16² + cylinder |
| Developing square-duct flow, Re_Dh = 50 (Shah & London) | Darcy f·Re | 56.91 | 56.86 (−0.1 %) | 50×12×12 |
| Lid-driven cube, Re = 100 | u at the cube centre | qualitative | −0.199 | 24³ |
| Sphere on a duct axis, Re_D = 40, 8.7 % blockage | C_d; side forces | ≈ 1.74 unconfined | 2.09 (blockage + staircase); Fy, Fz ≈ 1e-18 | 60×18×18 |
| Heated block in a 3-D duct | heat from block vs. enthalpy flux at outlet | equal | within 3 % (test) | 24×8×8 |
| FFT-preconditioned CG vs. sparse LU | velocity after 10 steps, sphere in duct | identical | < 1e-6, ∇·u < 1e-6 | 20×10×10 |

**External backends** (real tools in Docker: OpenFOAM v2406, DOLFINx v0.9.0, PyFR 2.0.3) **and their Python fallbacks**

| Backend | Case | Reference | Result |
|---|---|---|---|
| OpenFOAM simpleFoam | Ghia cavity, 32², u(0.5, 0.5) | −0.2058 | −0.2029, 127 SIMPLE iterations, 8 s |
| OpenFOAM buoyantBoussinesqSimpleFoam | de Vahl Davis Ra = 10⁵, 32², Nu (hot wall) | 4.519 | 4.65 (+2.9 %) |
| OpenFOAM simpleFoam + scalarTransport | electronics cooling (2 chips) | — | converged in 730 iterations, Q = 17.8 / 3.3 W/m |
| OpenFOAM pimpleFoam | Schäfer–Turek cylinder start-up, parabolic `exprFixedValue` inlet | — | Fx from the `forces` FO, 3 frames |
| OpenFOAM simpleFoam 3-D | sphere + partial-depth box, parabolic 3-D inlet | symmetric | converged in 82 its, Fz ≈ 1e-9 |
| OpenFOAM simpleFoam k-ω SST | backward-facing step | — | converged in 196 iterations |
| FEniCSx (Q2/Q1, Newton) | Ghia cavity, 32² | −0.2058 | −0.197 (6 Newton its, 0.9 s) |
| FEniCSx | 3-D buoyant duct, box + sphere + symmetry; transient 2-D cylinder | — | converged in 5 Newton its; 31 steps |
| PyFR (p = 3) | Ghia cavity, 12² elements | −0.2058 | −0.1995 |
| PyFR (p = 2) | cylinder start-up (2-D), box in a 3-D duct (hexes) | — | completed, fluidforce + pseudostats parsed |
| Python FEM (FEniCSx fallback) | de Vahl Davis Ra = 10⁴, 24² | Nu 2.243 | 2.238 |
| Python AC (PyFR fallback) | Poiseuille Δp, 40×12 / Ghia centre, 24² | 4.64 / −0.2058 | 4.53 / −0.2006 |

Honest caveats

- **Cylinder in channel (Schäfer–Turek 2D-2, Re = 100)** is included as a *qualitative* demo of unsteady vortex shedding. With
  a staircase cylinder of 10 cells across and a blended scheme it gives Cd ≈ 3.42 (benchmark 3.23, +6 %), Strouhal ≈ 0.25
  (benchmark ≈ 0.295) and a lift amplitude of ≈ 0.65 (benchmark ≈ 1.0). Do not use it for quantitative drag/lift without
  mesh refinement; the numerical diffusion of the blended scheme and the stair-stepped boundary damp the wake.
- The staircase representation of curved obstacles is first-order accurate at the wall; refine the mesh around such bodies.

---

## Project layout

```
cfdflow/
├── README.md                    this file
├── docker-compose.yml           PostgreSQL + app
├── docker-compose.solvers.yml   overlay: app may start OpenFOAM / FEniCSx / PyFR containers (Docker socket)
├── docker/pyfr/Dockerfile       PyFR 2.0.3 + libxsmm runtime image
├── .env.example  Makefile  .gitignore  .dockerignore
├── backend/
│   ├── Dockerfile               multi-stage: builds the frontend, then the Python image
│   ├── requirements.txt / requirements-dev.txt
│   ├── app/
│   │   ├── main.py              FastAPI app, lifespan (DB init, scheduler), SPA hosting
│   │   ├── config.py            settings (CFD_* env vars)
│   │   ├── db.py  models.py     SQLAlchemy engine and ORM (projects, runs, monitor_samples, run_logs)
│   │   ├── schemas.py           Pydantic: the case definition + API payloads
│   │   ├── templates.py         built-in example cases
│   │   ├── worker.py            solver process entry point (python -m app.worker <run_id>)
│   │   ├── api/projects.py      projects, templates, /api/check
│   │   ├── api/runs.py          runs, monitors, logs, fields, probe-line, exports, WebSocket
│   │   ├── services/checks.py   pre-run validation and physics estimates
│   │   ├── services/run_manager.py   process scheduler / reaper
│   │   └── solver/
│   │       ├── grid.py          2-D mesh + obstacle rasterisation
│   │       ├── grid3d.py        N-D mesh, obstacle / source masks shared by every backend
│   │       ├── ns2d.py          the 2-D flow + energy solver
│   │       ├── ns3d.py          the N-D (3-D) solver + FFT-preconditioned CG pressure solve
│   │       ├── runner.py        run loop, stopping criteria, frames, summary
│   │       ├── post.py          result files (staggered / cell-centred), slices, probes, VTU/CSV export
│   │       └── backends/
│   │           ├── base.py          SolverBackend interface, result + summary helpers
│   │           ├── launcher.py      native / Docker / WSL launching, live log streaming
│   │           ├── native.py        built-in solver
│   │           ├── openfoam.py      OpenFOAM case writer, runner, reader (foamlib)
│   │           ├── fenicsx.py       FEniCSx backend; fenicsx_script.py = the DOLFINx program
│   │           ├── fem_fallback.py  Python finite-element solver (FEniCSx fallback)
│   │           ├── pyfr.py          Gmsh/ini writer, runner, VTU reader
│   │           └── ac_fallback.py   Python artificial-compressibility solver (PyFR fallback)
│   └── tests/                   2-D + 3-D solver benchmarks, backend / fallback tests, API/worker/WebSocket tests
└── frontend/
    ├── package.json  vite.config.ts  tsconfig.json
    └── src/
        ├── App.tsx  ProjectContext.tsx  api.ts  types.ts  styles.css
        ├── lib/        colormap, geometry rasterisation, field decoding + streamlines, theme
        ├── components/ GeometryCanvas, FieldCanvas, LineChart, MonitorCharts, ui
        └── pages/      ProjectsPage, SetupPage, RunsPage, ResultsPage
```

---

## Configuration

Environment variables (prefix `CFD_`, also read from `backend/.env`):

| Variable | Default | Meaning |
|---|---|---|
| `CFD_DATABASE_URL` | `postgresql+psycopg://cfd:cfd@localhost:5432/cfdflow` | SQLAlchemy URL (SQLite also works) |
| `CFD_DATA_DIR` | `./data` | where run results (`result.npz`, `latest.npz`, `worker.log`) are stored |
| `CFD_MAX_CONCURRENT_RUNS` | `2` | simultaneous solver processes (≈ one core each) |
| `CFD_MAX_CELLS` | `250000` | cases larger than this are rejected by the case check |
| `CFD_CORS_ORIGINS` | `["http://localhost:5173", …]` | JSON list; only needed if the UI is served from another origin |
| `CFD_FRONTEND_DIST` | unset | directory of the built UI; when set the API serves it (set automatically in Docker) |
| `CFD_SCHEDULER_INTERVAL` | `0.5` | seconds between scheduler ticks |
| `CFD_OPENFOAM_LAUNCHER` / `CFD_FENICSX_LAUNCHER` / `CFD_PYFR_LAUNCHER` | `auto` | `auto` (PATH → Docker image → WSL), `native`, `docker`, `wsl`, `off` |
| `CFD_OPENFOAM_DOCKER_IMAGE` | `opencfd/openfoam-default:2406` | OpenFOAM image |
| `CFD_OPENFOAM_BUOYANT_SOLVER` | `boussinesq` | `compressible` = buoyantSimpleFoam family with the Boussinesq EoS |
| `CFD_FENICSX_DOCKER_IMAGE` / `CFD_FENICSX_PYTHON` | `dolfinx/dolfinx:v0.9.0` / `python3` | DOLFINx image / native interpreter that has `dolfinx` |
| `CFD_PYFR_DOCKER_IMAGE` / `CFD_PYFR_DEVICE` | `cfdflow/pyfr:2.0.3` / `openmp` | PyFR image (`make solver-images`) / PyFR compute backend |
| `CFD_*_WSL_DISTRO` | empty | WSL distribution for the `wsl` launcher |
| `CFD_MAX_FEM_CELLS` | `40000` | cell limit of the Python finite-element fallback |
| `CFD_DOCKER_PATH_MAP` | unset | `/data=/host/path`: when the app itself runs in Docker and uses the host's Docker socket |
| `CFD_DOCKER_AS_ROOT` | `0` | on Linux hosts solver containers run as the current uid unless set to `1` |

**Running the containerised app with external solvers:**
`CFD_HOST_DATA_DIR=$PWD/rundata docker compose -f docker-compose.yml -f docker-compose.solvers.yml up --build`. This installs
the Docker CLI in the image, mounts the Docker socket, and bind-mounts the run directory at the same host path that the
solver containers mount. Mounting the Docker socket gives the app control of the host's Docker; use it only on trusted hosts.

Tables are created on start-up (`Base.metadata.create_all`). For a production deployment that must evolve its schema, add
Alembic migrations (not included).

---

## REST / WebSocket API

Interactive docs: **`/docs`** (Swagger UI) and `/openapi.json`.

| Method & path | Purpose |
|---|---|
| `GET /api/health` | liveness |
| `GET /api/templates` | example cases |
| `POST /api/check` | validate an unsaved case → `{ok, items[], stats}` (stats include the backend that will run) |
| `GET /api/solvers` | solver backends: capabilities, availability (`mode`, `detail`), fallback |
| `GET /api/projects/{id}/export/{openfoam\|fenicsx\|pyfr}` | zip of the generated input deck (OpenFOAM case + `Allrun`, DOLFINx script + `case.json`, PyFR mesh + `.ini` + run script) |
| `GET/POST /api/projects`, `GET/PUT/DELETE /api/projects/{id}`, `POST …/duplicate` | project CRUD (PUT validates the whole case; 422 lists the offending fields) |
| `POST /api/projects/{id}/runs`, `GET …/runs` | start (409/422 on errors) / list runs |
| `GET /api/runs/{id}` | status, progress, summary, case snapshot |
| `POST /api/runs/{id}/cancel`, `DELETE /api/runs/{id}` | stop / delete |
| `GET /api/runs/{id}/monitors?since=&max_points=` | residual + monitor history (down-sampled for plotting) |
| `GET /api/runs/{id}/logs?after=` | console lines |
| `GET /api/runs/{id}/frames` | frame times (transient) |
| `GET /api/runs/{id}/fields?frame=&axis=&index=` | cell-centred fields, base64 float32, plus ranges and the solid mask; 3-D: the slice normal to `axis` (0/1/2) at layer `index` (default: mid x–y plane), with `axes`, `vectors` and `plane` info |
| `GET /api/runs/{id}/probe-line?x0=&y0=&x1=&y1=&n=&frame=&axis=&index=` | profile along a line (in the coordinates of the viewed plane) |
| `GET /api/runs/{id}/export/{vtu\|csv\|monitors}` | ParaView / CSV exports |
| `WS /api/runs/{id}/stream?since=&after=` | live status, new monitor samples and log lines (every 0.5 s) until the run ends |

---

## The case definition

A project's `config` (see `backend/app/schemas.py`, mirrored in `frontend/src/types.ts`):

```jsonc
{
  "dimension": "2d",                     // or "3d"
  "solver":    { "backend": "native", "allow_fallback": true, "order": 2, "processes": 1 },
  "material":  { "density": 1.204, "viscosity": 1.825e-5, "conductivity": 0.0257, "specific_heat": 1005, "thermal_expansion": 3.43e-3 },
  "domain":    { "width": 0.2, "height": 0.05 },
  "mesh":      { "nx": 120, "ny": 30 },
  "models":    { "energy": true, "buoyancy": false, "gravity_x": 0, "gravity_y": -9.81, "reference_temperature": 293.15 },
  "obstacles": [ { "id": "chip", "shape": "rectangle", "x": 0.06, "y": 0.0125, "width": 0.02, "height": 0.025,
                   "thermal": "fixed", "temperature": 343.15 } ],
  "heat_sources": [ { "id": "src1", "x": 0.1, "y": 0.02, "width": 0.01, "height": 0.01, "power_density": 1e4 } ],
  "boundaries": {
    "left":  { "type": "inlet",  "velocity": 0.2, "profile": "parabolic", "temperature": 293.15 },
    "right": { "type": "outlet" },
    "top":   { "type": "wall" },
    "bottom":{ "type": "wall", "thermal": "fixed", "temperature": 300 }
  },
  "initial":   { "velocity_x": 0.2, "velocity_y": 0, "temperature": 293.15 },
  "run":       { "mode": "steady", "tolerance": 1e-5, "cfl": 0.5, "max_iterations": 50000, "monitor_interval": 5 },
  "probes":    [ { "id": "exit", "name": "Near outlet", "x": 0.18, "y": 0.025 } ]
}
```

3-D additions: `domain.depth`, `mesh.nz`, `boundaries.back` / `boundaries.front` (z = 0 / z = depth), `obstacles[].z` /
`.depth` (null = mid-depth / full depth) and `shape: "sphere"`, the same `z` / `depth` on heat sources, `probes[].z`,
`models.gravity_z`, `initial.velocity_z`. `models.turbulence`: `laminar` | `k_epsilon` | `k_omega_sst` (OpenFOAM).
All new fields have defaults, so existing 2-D projects load unchanged.

Conventions: SI units; temperatures in K (only differences matter); inlet speed is the *mean* normal speed into the domain;
`wall_velocity` is tangential (+x on top/bottom walls, +y on left/right walls); outlets are 0 Pa gauge; forces are per metre
of depth; pressure is reported as `ρ · p_kinematic` (gauge).

---

## Testing

```bash
make test            # or individually:
cd backend  && python -m pytest -q tests     # 40 tests (+3 opt-in), ≈ 70 s
make test-external                           # also runs OpenFOAM / DOLFINx / PyFR for real (Docker images)
cd frontend && npm test && npm run typecheck # 6 unit tests + strict TypeScript
```

- `tests/test_solver.py` — Ghia cavity, Poiseuille Δp and profile, de Vahl Davis Nusselt number, heated-obstacle energy
  balance, enclosed pockets / pinning, transient run with frames and obstacle loads.
- `tests/test_solver3d.py` — N-D solver identical to the 2-D solver, extruded cavity between symmetry planes, square-duct
  f·Re, spectral-CG vs. LU, 3-D sphere loads/probes, 3-D energy balance, slices and 3-D exports, Python FEM (Nusselt,
  cavity, 3-D), Python AC (Poiseuille Δp, cavity).
- `tests/test_backends.py` — backend registry and fallback resolution, capability checks, OpenFOAM case generation (2-D and
  3-D, solver selection by physics, turbulence files, topoSet sources, fvOptions), foamlib field reading, FEniCSx export,
  PyFR mesh/ini generation, VTU reader. **Opt-in** (`CFD_TEST_EXTERNAL=1`): the Ghia cavity through real OpenFOAM, DOLFINx
  and PyFR, compared with the benchmark (all three pass, 28 s).
- By default `conftest.py` sets the external launchers to `off`, so the suite is deterministic and exercises the fallbacks.
- `tests/test_api.py` — template validity, validation errors, CRUD, a **real run** through the scheduler and worker
  subprocess, fields/probe/export endpoints, cancel of a running job with partial results, WebSocket streaming, a 3-D run with slices / probe line / VTU / CSV,
  runs that request OpenFOAM / FEniCSx / PyFR and fall back to Python, the "fallback disabled" refusal, case-file zip export.
- Run the API tests against PostgreSQL by setting `CFD_TEST_DATABASE_URL=postgresql+psycopg://…` (they default to SQLite).
- `frontend/src/lib/geometry.test.ts` — rasterisation parity with the backend, colour map, number formatting, streamline
  regression test.

---

## Limitations and roadmap

Current limitations

- Incompressible, constant properties, single fluid; uniform Cartesian mesh with staircase obstacles (all backends use the same
  mesh: OpenFOAM, DOLFINx and PyFR get the CFDFlow mesh, not body-fitted or refined meshes).
- Turbulence (RANS) only with the OpenFOAM backend; the native and Python solvers are laminar.
- PyFR backend: isothermal (its AC system has no energy equation); steady cases are integrated in time.
- External results are sampled onto the CFDFlow grid (cell centres); the native files in `data/runs/<id>/openfoam|fenicsx|pyfr/`
  keep the full-resolution solution for ParaView.
- The FEniCSx script targets DOLFINx 0.9 (0.10 changed the nonlinear-solver API).
- The Python FEM fallback is diffusive on coarse meshes (first-order-like convergence of equal-order stabilisation) and limited
  to `CFD_MAX_FEM_CELLS`; the AC fallback converges more slowly than the projection solver (explicit pseudo-time).
- 3-D geometry is edited in the x–y projection; z-placement is entered numerically.
- Explicit time stepping: stiff or very fine meshes need many small steps (the case check estimates cost).
- No Alembic migrations, no authentication / multi-user separation (projects are global to the deployment); put it behind a reverse proxy with auth if exposed.
- The Docker image definition and compose file are provided and were validated structurally (`docker compose config`), but the
  image was **not built** in the authoring environment (no Docker daemon). The same steps — `pip install -r requirements.txt`,
  `npm ci && npm run build`, `uvicorn` serving the built UI against PostgreSQL 16 — were executed and tested natively.

Roadmap ideas (the code is organised for them)

1. Turbulence models (k-ε / k-ω SST) and wall functions.
2. Gmsh / snappyHexMesh meshing and an imported-geometry (STL) workflow, feeding the existing OpenFOAM / FEniCSx / PyFR backends.
3. ~~A `SolverBackend` interface with OpenFOAM / FEniCSx adapters for 3-D~~ — done (plus PyFR). Next: compressible solvers
   (rhoPimpleFoam, PyFR `navier-stokes`) and an interactive 3-D (WebGL) viewer alongside the slices.
4. Semi-implicit time stepping (implicit diffusion) for faster steady convergence; multigrid pressure solver for >10⁶ cells.
5. Porous media / MRF cell zones, species transport, dynamic probes, report templates, batch runs and run comparison.
6. Authentication, per-user projects, Alembic migrations, object storage for results.

---

## Work log — what was built and verified

1. **Research.** Read the BaramFlow manual (introduction and manual index): BARAM is an OpenFOAM-based GUI (Python/PySide6/VTK)
   with baramMesh and baramFlow modules; workflow sections General → Models → Materials → Cell zones → Boundary conditions →
   Initialization → Run conditions → Monitor → Run → Graphics → Report. Surveyed Python-integrable CFD packages
   (FEniCSx/Oasis, PyFR, SU2, Gmsh, meshio, NumPy/SciPy) and chose the stack described [above](#why-this-solver-stack).
2. **Solver.** Implemented the MAC-grid projection solver with blended convection, immersed obstacles, boundary conditions,
   energy equation, Boussinesq buoyancy, heat sources, obstacle loads, and the direct steady-energy solve. Verified against
   Ghia (cavity), analytic Poiseuille flow, de Vahl Davis (natural convection) and an energy balance (see [Validation](#validation)).
   Found and fixed slow thermal convergence of steady forced-convection cases by introducing the decoupled direct energy solve.
3. **Backend.** FastAPI + SQLAlchemy models (`projects`, `runs`, `monitor_samples`, `run_logs`; JSONB on PostgreSQL), Pydantic
   case schema with validation, case-check service, seven example cases, process-per-run scheduler with queueing / cancel /
   crash & restart recovery, REST API, WebSocket streaming, field/probe/export endpoints (base64 float32, `meshio` VTU).
   Tests: 13 passing on SQLite **and** on a real PostgreSQL 16 server (including the worker subprocess, cancellation and WebSocket).
4. **Frontend.** React/TypeScript app with a project dashboard, guided setup workspace (interactive geometry canvas, live checks,
   autosave), run monitor (residuals, monitors, console, live preview) and results viewer (contours, vectors, streamlines, line
   probes, animation, exports). Strict TypeScript, 6 unit tests, production build ≈ 106 kB gzip.
5. **End-to-end browser testing** with headless Chromium against the production build and PostgreSQL: lid-driven cavity,
   cylinder in channel (animation, line probe), electronics cooling (temperature field, dark theme). Bugs found and fixed this way:
   flexbox shrinking the field canvas, degenerate chart ranges causing an infinite tick loop, streamline x/y order bug and
   its per-frame cost (replaced by an evenly-spaced bin-occupancy algorithm; added a regression test), canvas label overlap.
7. **3-D and solver backends (this update).**
   - Schema: `dimension`, depth / `nz`, front/back boundaries, z-placement, spheres, `gravity_z`, `velocity_z`, `turbulence`,
     `solver`; all with defaults, so existing projects load unchanged.
   - `grid3d.py` and `ns3d.py`: dimension-generic MAC projection solver. Verified to be identical to `ns2d.py` in 2-D and to
     the 2-D flow between symmetry planes; square-duct f·Re within 0.1 %. Found and fixed: 0/0 residuals of an identically
     zero component (w) that prevented convergence; a time-step mismatch in the comparison test. Added the
     FFT-preconditioned CG solver after measuring that LU set-up took 7 s at 32³.
   - `backends/`: `SolverBackend` interface, launcher (native / Docker / WSL, live log parsing, graceful stop), registry
     with fallbacks, backend-aware case check, worker dispatch, `/api/solvers`, case-file export.
   - OpenFOAM: pulled `opencfd/openfoam-default:2406` and ran every path for real: cavity, cylinder (exprFixedValue,
     subsetMesh, forces), forced convection, natural convection, 3-D sphere + box, k-ω SST. Fixes found that way: the
     image's entrypoint changes directory (launcher now `cd /case`), `cd "${0%/*}"` fails when run as `bash Allrun`,
     plain-SIMPLE buoyantSimpleFoam stalls (→ scalarTransport path), and the variable-density buoyant solver disagrees with
     the Boussinesq benchmark at β·ΔT = 1 (→ buoyantBoussinesq* solvers by default).
   - FEniCSx: wrote the DOLFINx program and ran it in `dolfinx/dolfinx:v0.9.0` (2-D cavity, 3-D buoyant duct, transient
     cylinder). Fixes: point-evaluation shapes, UTF-8 `case.json`, continuation logic. Wrote the Python FEM fallback
     (verified against de Vahl Davis, refinement study on Ghia) and made its 3-D LU 3× faster with a banded ordering.
   - PyFR: built `docker/pyfr` and ran cavity (p = 3), cylinder and a 3-D box case for real. Fixes: libxsmm must be a
     shared library on the loader path, the pinned libxsmm commit segfaults with PyFR 2.x, `fluidforce` needs `quad-deg`,
     `dt` must divide the output interval, the pseudo-step needed the acoustic CFL plus p-multigrid, and PyFR's VTK 2.1
     output needed a custom reader. Wrote and verified the AC fallback (Poiseuille, Ghia).
   - Frontend: 2-D/3-D switch, solver panel (availability, fallback, order, MPI ranks, case download), 3-D inputs, slice
     viewer (plane + position), w field, Fz and 3-D units. Checked in the built app with headless Edge against a live server
     whose worker ran a native 3-D case and an OpenFOAM case in Docker.
   - Tests: 40 backend tests (+3 opt-in real-solver tests, passing), 6 frontend tests, strict TypeScript.
8. **Packaging.** Multi-stage Dockerfile, docker-compose (PostgreSQL 16 + app), `.env.example`, Makefile, this README, and the
   downloadable archive.
