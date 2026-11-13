# Gearhead — FEA · CFD · CAD in one browser workspace

Gearhead is a full-stack web application that brings three engineering tools together behind **one account, one
subscription and one project file**:

| Workspace | What it does | Came from |
|---|---|---|
| **FEA · Structures** | Finite element analysis with a standalone solver: geometry and meshing (built-in or Gmsh), CAD import, parts and materials, supports and loads, 8 analysis types, PrePoMax-style post-processing, model stack, AI assistant and guide | `webfea` (Gearhead FEA) — the host platform |
| **CFD · Fluids** | 2-D / 3-D incompressible flow and heat transfer, BaramFlow-style setup, native finite-volume solver plus OpenFOAM, FEniCSx and PyFR backends (with pure-Python fallbacks), live run monitoring, flow results | `cfdflow` (Gearhead CFD) |
| **CAD · Design** | Parametric CAD/CAM: 2-D sketches with a constraint solver, 3-D feature modelling, assemblies with mates, drawing sheets, CAM toolpaths and G-code, CAD simulation (native and CalculiX) | `cadweb` (Gearhead CAD) |

You switch workspaces with the **Functions** button at the left of the ribbon (**FEA · CFD · CAD**). It replaces
webfea's earlier CAD / CAM / CAE switcher. The website, sign-up and login, password reset, plans and **GCash (PayMongo)
billing** are webfea's, unchanged. Every backend feature of the three original projects is kept (see
[What was kept from each project](#14-what-was-kept-from-each-project)). The workspaces also exchange geometry and
loads with each other (see [Working across workspaces](#5-working-across-workspaces)).

```
 Browser (React 18 + Three.js)                         FastAPI (one process)                       PostgreSQL
 ┌──────────────────────────────────────┐   REST    ┌──────────────────────────────────────┐   ┌─────────────────┐
 │ site · login · account · billing     │──────────▶│ auth · billing (PayMongo / mock)     │──▶│ users, sessions, │
 │ /app  ribbon [FEA|CFD|CAD] + Transfer│           │ /api/projects…  FEA  (app/fea)        │   │ payments,        │
 │  ├ FEA body (JSX)                    │           │ /api/cfd/…      CFD  (app/cfd)        │   │ projects (FEA),  │
 │  ├ CFD body (TSX, shadow root)       │ WebSocket │ /api/cad/…      CAD  (app/cad)        │   │ cfd_*, cad_*     │
 │  └ CAD body (TSX, shadow root)       │──────────▶│ /api/projects/{id}/bridge/…           │   └─────────────────┘
 └──────────────────────────────────────┘           └──────┬─────────────────┬──────────────┘
                                                           │ one OS process  │ Gmsh subprocess, CalculiX,
                                                           ▼ per CFD run     ▼ OpenFOAM / DOLFINx / PyFR (optional)
                                                     backend/data/cfd   backend/data/cad (STEP uploads)
```

---

## Contents
1. [Quick start](#1-quick-start)
2. [The workspace](#2-the-workspace)
3. [Projects: one project, three workspaces](#3-projects-one-project-three-workspaces)
4. [The project file (`.gearhead.json`)](#4-the-project-file-gearheadjson)
5. [Working across workspaces (Transfer)](#5-working-across-workspaces)
6. [The FEA workspace](#6-the-fea-workspace)
7. [The CFD workspace](#7-the-cfd-workspace)
8. [The CAD workspace](#8-the-cad-workspace)
9. [Accounts, plans and billing](#9-accounts-plans-and-billing)
10. [Architecture and repository layout](#10-architecture-and-repository-layout)
11. [Configuration](#11-configuration)
12. [API reference (summary)](#12-api-reference-summary)
13. [Testing](#13-testing)
14. [What was kept from each project](#14-what-was-kept-from-each-project)
15. [Deployment](#15-deployment)
16. [Troubleshooting](#16-troubleshooting)
17. [Limitations](#17-limitations)
18. [Documentation map](#18-documentation-map)
19. [License and attribution](#19-license-and-attribution)

---

## 1. Quick start

### 1.1 Docker (PostgreSQL + API + nginx)
```bash
cp .env.example .env              # set POSTGRES_PASSWORD (and PayMongo / SMTP / AI keys when you have them)
docker compose up --build         # http://localhost:5173
```
The backend image includes Gmsh, OpenCascade (cadquery) and CalculiX. CFD results and uploaded CAD files are kept in the
`appdata` volume, and the database in `pgdata`. To let the CFD workspace start OpenFOAM / FEniCSx / PyFR containers, see
[15.2](#152-external-cfd-solvers-in-docker).

### 1.2 Local development
Prerequisites: Python 3.12+ (tested on 3.14), Node 20+, and optionally PostgreSQL 16 (SQLite is the zero-config default).

```bash
# backend - http://localhost:8000 (interactive API docs at /docs)
cd backend
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                    # FastAPI, NumPy/SciPy, Gmsh, cadquery (OpenCascade), meshio, foamlib…
uvicorn app.main:app --reload --port 8000              # SQLite file backend/webfea.db unless DATABASE_URL is set

# frontend - http://localhost:5173 (second terminal; Vite proxies /api and the CFD WebSocket to :8000)
cd frontend
npm install
npm run dev

# an account without the sign-up form (optionally with Pro)
cd backend && python -m scripts.create_user you@example.com --pro-days 365
```
Payments default to a mock GCash screen in development (`PAYMENT_PROVIDER=mock`). Linux needs a few shared libraries
for OpenCascade / Gmsh: `libgl1 libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1`.

> `uvicorn --reload` is fine for development. In production run **one** worker process (see [15](#15-deployment)).

### 1.3 First ten minutes
1. Sign up, open **Workspace**, and create a project with **Project ▾ → + New**.
2. **FEA**: use **Geometry ▾** to mesh a box. Add supports and loads in the *Analysis* tab, then **Solve**.
3. **Transfer ▾ → "→ CFD" → Send to CFD** creates a CFD case: a wind tunnel sized around the FEA body.
4. Switch **Functions → CFD · Fluids**. The case's *Setup* shows the body on the CFD mesh. Go to **Run**, then
   **Start new run**, and watch the residuals stream in. **Results** shows the flow.
5. Back in **FEA**, use **Transfer ▾ → "CFD loads" → Apply to the active FEA model**. The surface pressure becomes FEA
   pressure loads. Solve again.
6. **Functions → CAD · Design**, then **Demo**. Explore *Sketch*, *Model*, *Assembly*, *Drawing*, *CAM* and *Simulate*.
   **Transfer → "→ FEA"** meshes the mounting plate into a new FEA model with its aluminium material.
7. **Project ▾ → Save…** writes everything (FEA models, CFD cases and CAD documents) into one `.gearhead.json` file.

---

## 2. The workspace
The layout follows webfea's Fusion 360-style workspace (app bar, command ribbon, floating command dialogs, status bar).

```
┌ app bar ─ logo [FEA|CFD|CAD] · project · model / case · status · (AI Assistant · Guide: FEA) · account · theme ─┐
├ ribbon ─ [Functions ▾ FEA · Structures] │ commands of the current workspace …                    │ Transfer ▾ ┤
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                         body of the current workspace (FEA / CFD / CAD)                                       │
├ messages ─────────────────────────────────────────────────────────────────────────────────────────────────────┤
└ status bar ─ job / mesh · "FEA n models · CFD n cases · CAD n parts" · workspace · project ────────────────────┘
```

**Functions button.** It sits at the left end of the ribbon and picks **FEA · Structures**, **CFD · Fluids** or
**CAD · Design**. The workspace is part of the URL (`/app`, `/app/cfd/…`, `/app/cad`), so the browser's back and
forward buttons and bookmarks work. Switching never loses work: FEA keeps its state in memory, and CFD and CAD save to
the server as you edit.

**Ribbon per workspace**

| Workspace | Commands (▾ = opens a dialog; others act directly) |
|---|---|
| FEA | Project▾ · Undo · Redo · Geometry▾ · Frames▾ · Parts▾ · Materials▾ · Select▾ · Stock▾ · Solve · Transfer▾ |
| CFD | Project▾ · Cases · Setup · Run · Results · Transfer▾ |
| CAD | Project▾ · Sketch · Model · Assembly · Drawing · CAM · Simulate · Demo · Transfer▾ |

The commands *Project* and *Transfer* exist in every workspace. The command for the page currently showing is
highlighted (the CFD page, or the CAD sub-workspace). Dialogs float, can be dragged, close with Esc and keep their
half-filled values. In CFD and CAD they open at the top right so they do not cover those workspaces' left-hand panels.

**Theme.** The light ("vellum") and dark ("blueprint") themes apply to all three workspaces at once. The ☾ / ☀ button
is in the app bar.

**How the three front ends live together.** The FEA workspace is webfea's JSX. The CFD and CAD workspaces are the
original TypeScript front ends of cfdflow and cadweb, rendered by `components/ShadowPane.jsx` inside **shadow roots**.
Their stylesheets style bare elements and reuse class names that FEA also uses (`.viewport`, `.toolbar`, `.legend` …),
so the shadow root keeps the three style sheets apart. They stay in the same React tree, sharing the router, auth and
project state. The page's `--gh-*` design tokens and fonts are inherited into the shadow roots, and the theme attribute
is mirrored onto the shadow host. CFD and CAD are separate bundle chunks, loaded the first time their workspace opens.

---

## 3. Projects: one project, three workspaces
A **Gearhead project** belongs to one user and counts once against the plan's project limit. It holds:

| Workspace | Stored as | Notes |
|---|---|---|
| FEA | the `projects` row (the active model) + `model_layers` (the other models of the **model stack**) + `project_history` (undo) + `jobs` (runs) | as in webfea |
| CFD | any number of **cases** in `cfd_cases` (`project_id` → `projects.id`), with `cfd_runs`, `cfd_monitor_samples`, `cfd_run_logs`; result files in `CFD_DATA_DIR/runs/<run id>/` | a case is what CFDFlow called a "project" |
| CAD | one **CAD project** in `cad_projects` (`project_id` → `projects.id`, unique, created on first use), with `cad_sketches`, `cad_parts`, `cad_assemblies`, `cad_assembly_components`, `cad_mates`, `cad_drawings`, `cad_cam_setups`, `cad_simulation_jobs`; uploaded STEP files in `CAD_DATA_DIR/uploads/` | parametric definitions; B-reps are rebuilt on demand |

- **Ownership.** Every CFD and CAD route requires a signed-in user. Rows of other users answer **404**, not 403, so ids
  cannot be probed (the same rule as webfea). Enforcement is central: an async dependency (`app/scope.py`) binds the
  user to the request, and the CFD lookups (`_get`, `_run`) and CAD lookup (`get_or_404`) walk each row up to its
  Gearhead project and check the owner. The CFD WebSocket authenticates from the session cookie and checks the Origin
  header.
- **Deleting a project** removes its FEA models, CFD cases (and their run directories) and its CAD project (and its
  uploaded STEP files).
- **Stand-alone API calls still work.** `POST /api/cfd/projects` and `POST /api/cad/projects` accept an optional
  `project_id`. Without it, a new Gearhead project with the same name is created for the case or CAD project, counted
  against the plan limit.

---

## 4. The project file (`.gearhead.json`)
**Project ▾ → Save…** downloads the whole project as one JSON file. Any workspace can open it, so a file made while
working on FEA carries its CFD and CAD content too, and vice versa.

```jsonc
{
  "format": "gearhead-project", "version": 3, "workspaces": ["fea", "cfd", "cad"],
  "name": "Bracket study", "description": "",
  // FEA - exactly as version 2: the active model's keys at the top level, the whole model stack in "layers"
  "nodes": [...], "elements": [...], "element_type": "C3D10", "material": {...}, "parts": [...], "loads": [...], ...
  "layers": [{ "name": "Model 1", "process": "cae", "visible": true, "active": true, "model": {...} }, ...],
  // CFD - one entry per case (case definitions; runs are recomputed)
  "cfd": { "cases": [{ "name": "Flow around Box", "description": "", "template": null, "config": { /* CFD case */ } }] },
  // CAD - definitions with their original ids (remapped on import) + the STEP files parts were built from
  "cad": { "name": "...", "sketches": [...], "parts": [...], "assemblies": [{ "components": [...], "mates": [...] }],
           "drawings": [...], "cam_setups": [...], "uploads": { "<32 hex>.step": "<base64>" } }
}
```

| Action | Where | What happens |
|---|---|---|
| **Save…** | Project dialog (all workspaces) | Writes `<name>.gearhead.json` (`GET /api/projects/{id}/export/project`) |
| **Open…** | Project dialog | Creates a **new project** from a file (`POST /api/projects/import`) |
| **Open into this project…** | Project dialog | **Adds** a file's FEA models (on top of the stack), CFD cases and CAD documents to the open project (`POST /api/projects/{id}/merge-file`) |
| **Export .inp** | Project dialog (FEA) | CalculiX/Abaqus-style deck of the active FEA model (unchanged) |

- **Readers.** Version 1 and 2 webfea files (`"format": "gearhead-fea-project"`, `.gfea.json`) still open, FEA only.
  The FEA keys of version 3 are laid out exactly like version 2.
- **On import.** CAD rows get new ids. Every reference is remapped: feature parameters of type *sketch*,
  *sketch list* and *part* (resolved generically from the feature schemas), drawing views, assembly components and
  mates, and CAM setups. Embedded STEP files are stored under fresh names. CFD case definitions are validated against
  the case schema.
- **Not in the file**: results (FEA jobs, CFD runs, CAD simulation jobs), FEA undo history and CAD G-code output. They
  are recomputed, as webfea files always did.

---

## 5. Working across workspaces
**Transfer ▾** (in every ribbon) opens one dialog with four tabs. Behind it is `backend/app/bridge.py`
(`/api/projects/{id}/bridge/*`).

| Tab | From | To | How |
|---|---|---|---|
| **→ FEA** | CAD part, CAD assembly, or the obstacles of a CFD case | A new model on the FEA stack (or the active model) | Exact STEP (mm) → Gmsh mesh: Tet4 / Tet10 / Hex8 / Hex20, element size or automatic. An assembly gives one part per component (tied automatically by the FEA solver). **CAD materials become FEA materials** (E, ν, density, conductivity, specific heat, yield), one library entry per material. |
| **→ CFD** | FEA model (each solid part), CAD part, CAD assembly (each component) | A **new CFD case** (wind tunnel) or an existing case | Each body becomes a **`mesh` obstacle**: a closed triangle surface (metres), decimated to `CFD_MAX_OBSTACLE_TRIANGLES`. A new case sizes the domain around the bodies (2 L upstream, 4 L downstream, 1.5 L to the sides; L is the largest body extent), picks a mesh resolution within a cell budget, sets inlet left / outlet right / symmetry sides, uses air, and chooses an inlet speed for Re ≈ 100 (editable). 2-D or 3-D; optional fixed surface temperature. |
| **→ CAD** | FEA model, or CFD obstacles | CAD parts | FEA **primitives become editable parametric features** at their current position: box, cylinder, sphere, cone, tube (cylinder + bore cut), plate with hole (box + hole cut), L-bracket (block + cut-out). A torus becomes an exact OpenCascade solid. Imported or meshed parts become a solid **sewn from their surface triangles** (STEP). CFD boxes, cylinders and spheres become features; imported solids go back to their source position. |
| **CFD loads** | A finished CFD run + one obstacle | The **active FEA model** | Fluid **pressure** on each surface face (sampled in the first fluid cell next to it) becomes FEA **pressure loads**. The fluid **temperature** next to the wall becomes FEA **temperature supports** (energy runs). Each is grouped into N levels (default 12), one load or support per level with that level's mean value, which keeps the model editable. |

**The `mesh` obstacle (new CFD shape).** It is rasterised with CFDFlow's rule, *cell centre inside the solid*, using a
ray-parity test (`app/cfd/solver/meshsolid.py`), so every backend sees the same staircase body:
- native 2-D / 3-D solver and the Python fallbacks: through the shared `grid3d.obstacle_mask`;
- **OpenFOAM**: a union of `boxToCell` boxes on cell faces. It is geometric rather than cell labels, so the per-obstacle
  `subsetMesh` renumbering does not matter;
- **FEniCSx**: the DOLFINx program gets the solid cell list in `case.json`;
- **PyFR**: through the CFDFlow grid it writes;
- **the Setup canvas** (TypeScript): the same section and ray offsets as the backend, so the coloured cells are the
  solver's cells.

In 2-D, the section through the body's centre is used. In 3-D, the whole body is used, and the canvas shows its centre
section. You can move a mesh obstacle by dragging it or editing its centre. Its shape comes from FEA or CAD: edit it
there and send it again.

**Coordinates and units.** FEA and CFD work in metres, CAD in millimetres; the bridge converts. A body sent to CFD
remembers the centre of its source bounding box (`obstacle.source.ref_centre`). Results are mapped back with
`cfd_point = fea_point − ref_centre + obstacle_centre`, so loads land on the right faces even after the body was moved
inside the wind tunnel. The active FEA model must be the geometry the obstacle came from.

---

## 6. The FEA workspace
Everything webfea did, unchanged ([solver reference](docs/SOLVER_REFERENCE.md),
[workspace layout](docs/WORKSPACE_RIBBON_AND_MODEL_STACK.md)):

- **Pre-processing**: box, cylinder, sphere, tube, plate with hole, L-bracket, cone and torus. Every element type works
  for every shape: Tet4 / Tet10 / Hex8 / Hex20, with the built-in mesher **or Gmsh** (algorithms, size fields,
  optimisers, local refinement). Also: **CAD import** (STEP / IGES / BREP / STL); mesh import (`.inp`, Gmsh `.msh`
  v2 / v4); **beams, trusses, shells and springs**; multiple parts, each with its own material from a library;
  reference points; rigid, tie and **frictionless contact** constraints; named node / face sets; selection by click,
  box, coordinate range, face angle or id.
- **Supports & loads**: fixed, symmetry, prescribed displacement, temperature; force, pressure, traction, remote
  force / moment, gravity, centrifugal, thermal strain, heat source, flux, convection, radiation.
- **Analyses (8)**: static, nonlinear (J2 plasticity), frequency, linear buckling, dynamic (Newmark), steady heat,
  transient heat and thermo-mechanical, plus **NLGEOM**. Free plan: static only.
- **Post-processing**: contours (displacement, von Mises, Tresca, principal, components, plastic strain, temperature,
  heat flux), deformation scale and animation, undeformed ghost, section plane, min / max, probe, mode browser,
  time-frame playback, history charts, CSV / VTK / PNG export.
- **Model stack** (several models per project, like image layers), **undo / redo** (Ctrl+Z / Ctrl+Y, FEA only), the
  PrePoMax-style left panel (Geometry | Model | Analysis), PrePoMax viewport glyphs, the pick cursor and box selection.
- **✦ AI Assistant** (Gemini, with Anthropic or OpenRouter as fallbacks; needs a key on the server) and the **? Guide**
  walkthrough. Both are FEA-only and appear in the app bar while FEA is open.
- **Stock** (formerly the "CAM" sub-workspace's *Setup*): the stock block around the active model and the volume to
  remove. Full CAM is in the CAD workspace.

The former CAD / CAM / CAE sub-workspaces of FEA are merged into this one ribbon, so every command is always available.
Each model still keeps its old tag in files for compatibility; the Model panel shows it as GEO / CAM / FEA.

---

## 7. The CFD workspace
Everything CFDFlow did ([full CFD reference](docs/cfd/CFDFLOW_REFERENCE.md)), now scoped to the open project:

- **Cases** (ribbon *Cases*): the project's CFD cases, plus **11 validated examples** (lid-driven cavity, Poiseuille
  channel, cylinder vortex shedding, backward-facing step, natural convection, electronics cooling, lid-driven cube,
  square duct, sphere, heated chip, RANS backward-facing step) and a blank case. Duplicate and delete are available.
- **① Setup** (BaramFlow-style sections): General (2-D / 3-D, **solver backend**: native, OpenFOAM, FEniCSx, PyFR,
  each with a Python fallback, plus download of the generated input deck), Geometry (interactive canvas; rectangles,
  circles, spheres, **imported solids**, heat sources, probes, all draggable), Mesh, Models (energy, Boussinesq
  buoyancy, laminar / k-ε / k-ω SST via OpenFOAM), Materials, Boundary conditions, Initialization, Run conditions,
  Monitors. A **live case check** gives errors, physics warnings and a cost estimate. Changes **autosave**.
- **② Run & monitor**: each run is an isolated solver process with a frozen copy of the case. Bounded concurrency;
  stop at any time; crash and restart recovery. **Live WebSocket streaming** of residuals, probe / force /
  heat-transfer monitors, the console log and a flow preview.
- **③ Results**: velocity, pressure, temperature and vorticity contours, colour maps, vectors, streamlines, line
  probe, transient animation, 3-D slices (any plane, any position), obstacle loads, and exports (ParaView `.vtu`,
  CSV, monitors CSV, PNG).

Units are SI throughout. The flow is incompressible, single-phase and Newtonian, on a uniform Cartesian mesh with
staircase obstacles (see [Limitations](#17-limitations)).

---

## 8. The CAD workspace
Everything CADWeb did ([full CAD reference](docs/cad/CADWEB_REFERENCE.md)). The six top-bar tabs of the stand-alone
app are now ribbon commands:

- **Sketch**: lines, rectangles, circles, arcs, polylines, polygons, slots, ellipses, splines, points, construction
  geometry. **Trim / extend / fillet / explode**. A **geometric constraint solver** (19 constraint types, DOF report)
  with constraint inference and auto-constrain. Layers, transforms and patterns, profile analysis, and DXF import /
  export plus SVG.
- **Model**: primitives, extrude / revolve / sweep / loft, holes, fillets, chamfers, shell, patterns, mirror, combine,
  STEP import. A history-based feature tree (reorder, suppress, edit), **named parameters and expressions**
  (`=hole_d*2`), **persistent face / edge references**, mass properties, and STEP / STL / BREP export.
- **Assembly**: components, 9 mate types solved simultaneously (DOF / conflict report), interference, bill of
  materials, exploded view, STEP export.
- **Drawing**: sheets A4–A0 / Letter / Tabloid with a title block, HLR views in 1st / 3rd-angle projection,
  associative dimensions and auto-dimensioning, PDF and SVG export.
- **CAM**: stock, a tool library with holder envelopes, face / contour / pocket / drill / 3-D roughing / 3-D finishing,
  rest machining, feature recognition, a linker, collision checks, a material-removal simulation, and Fanuc /
  LinuxCNC / GRBL posts.
- **Simulate**: native linear static / thermal / modal (scikit-fem). With **CalculiX**: nonlinear (plasticity,
  NLGEOM), buckling, transient thermal, transient dynamic, and **assembly contact**.
- **Demo** (ribbon): adds CADWeb's demo (a parametric mounting plate, pins, an assembly with mates and a CAM setup) to
  the project's CAD workspace.

Units are mm, degrees, with Z up. The sketch shortcuts (Esc, Delete, Enter) work inside the shadow root.

---

## 9. Accounts, plans and billing
Unchanged from webfea ([details](docs/AUTH_AND_BILLING.md)): email + password (Argon2), server-side sessions in an
HttpOnly cookie, lockout after failed logins, password reset by email, an Origin check on state-changing requests
(now also on WebSocket handshakes), and prepaid **GCash** subscriptions through PayMongo (a mock provider in
development).

| | Free | Pro |
|---|---|---|
| Projects (each with FEA + CFD + CAD) | 3 (`FREE_MAX_PROJECTS`) | unlimited |
| FEA analyses | static | all 8 + NLGEOM |
| CFD, CAD, Transfer | included | included |

CFD cases and CAD documents live inside projects, so they are covered by the project limit. The CFD and CAD tools
have no extra plan gates; add them in `plans.py` if needed.

---

## 10. Architecture and repository layout
```
gearhead-suite/
├── README.md  .env.example  docker-compose.yml  docker-compose.solvers.yml  Jenkinsfile
├── docker/calculix/Dockerfile        optional CalculiX image (CAD Simulate), when ccx is not installed
├── docker/pyfr/Dockerfile            optional PyFR 2.0.3 image (CFD PyFR backend)
├── docs/                             webfea docs + docs/cfd/CFDFLOW_REFERENCE.md + docs/cad/CADWEB_REFERENCE.md
├── backend/
│   ├── requirements.txt              merged: FEA + CFD (meshio, foamlib) + CAD (cadquery, shapely, ezdxf, scikit-fem)
│   ├── app/
│   │   ├── main.py                   FastAPI app: lifespan (CFD scheduler), routers, FEA endpoints, project files
│   │   ├── config.py database.py models.py deps.py security.py   platform (one engine, one declarative Base)
│   │   ├── routers/ payments/ plans.py billing_service.py mailer.py    accounts & billing
│   │   ├── fea/ layers.py history.py jobs.py parts.py agent.py      FEA engine, model stack, undo, AI assistant
│   │   ├── scope.py                  request-scoped user for the CFD / CAD routers (ownership checks)
│   │   ├── workspaces.py             project ↔ CFD cases / CAD project glue, workspace summary, cascading delete
│   │   ├── projectfile.py            .gearhead.json v3: CFD and CAD sections (export, import with id remapping)
│   │   ├── bridge.py                 Transfer: FEA ⇄ CAD ⇄ CFD geometry, CFD results → FEA loads
│   │   ├── cfd/                      CFD workbench (formerly cfdflow/backend/app), routes under /api/cfd
│   │   │   ├── main.py               mount() + scheduler start/stop          worker.py  python -m app.cfd.worker <run>
│   │   │   ├── api/ projects.py runs.py    services/ checks.py run_manager.py    templates.py schemas.py models.py
│   │   │   └── solver/  grid.py grid3d.py meshsolid.py (new) ns2d.py ns3d.py runner.py post.py backends/…
│   │   └── cad/                      CAD / CAM workbench (formerly cadweb/backend/app), routes under /api/cad
│   │       ├── main.py               mount() + forward-only column migration of the cad_ tables
│   │       ├── routers/              meta projects sketches parts assemblies drawings cam simulations (deps.py = ownership)
│   │       └── services/             sketch2d sketch_edit modeling3d toponaming assembly mate_solver drawing
│   │                                 geometry_mesh cam gcode simulation calculix store demo
│   └── tests/                        FEA + platform suite, tests/cfd/, tests/cad/, test_suite_integration.py
└── frontend/
    ├── package.json vite.config.js tsconfig.json (type-checks src/cfd + src/cad) nginx.conf Dockerfile
    └── src/
        ├── App.jsx                   routes: site pages, auth, billing, /app/* → Workspace
        ├── pages/Workspace.jsx       the shell: app bar, ribbon per workspace, FEA body, CFD / CAD panes, dialogs
        ├── components/               FEA panels + Ribbon (FEA/CFD/CAD switcher) + BridgePanel (Transfer)
        │                             + ShadowPane (shadow-root host) + ProjectPanel (save / open / open into)
        ├── workspaces/CfdPane.jsx CadPane.jsx   lazy chunks: inject the CFD / CAD stylesheet into a shadow root
        ├── cfd/                      CFD front end (formerly cfdflow/frontend/src), TypeScript
        ├── cad/                      CAD front end (formerly cadweb/frontend/src), TypeScript
        └── site/ auth/ ui/ brand/ layout/ styles/      website, design system, tokens (shared by all three)
```

**Request flow.** One FastAPI process serves everything.
- FEA endpoints run the NumPy/SciPy engine in background threads; Gmsh runs in a subprocess with a time limit.
- The **CFD scheduler** (a thread started by the app's lifespan) launches one OS process per run
  (`python -m app.cfd.worker <run id>`). The worker writes progress into the database and result files under
  `CFD_DATA_DIR`. The API never runs CFD numerics itself.
- **CAD** rebuilds B-reps on demand (cadquery / OpenCascade, with a small build cache). CAD simulations run as
  background tasks. CalculiX runs as a separate program.

**Database.** One SQLAlchemy engine and one declarative `Base` for all tables. CFD and CAD tables are prefixed `cfd_`
and `cad_`, and their classes are prefixed `Cfd` / `Cad` where a name would clash; the original names remain as
aliases for the moved code. Tables are created at start-up. webfea's and cadweb's small forward-only column migrations
both run. On SQLite, WAL mode and a busy timeout let the CFD workers write while the API reads.

---

## 11. Configuration
Environment variables, read from the environment, `backend/.env` or the repository-root `.env`; real environment
variables win. See `.env.example` for the full list.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./webfea.db` | PostgreSQL in production (`postgresql://user:pass@host/db`) |
| `APP_ENV` / `APP_BASE_URL` | `development` / `http://localhost:5173` | production refuses mock payments, SQLite, http or insecure cookies |
| `PAYMENT_PROVIDER`, `PAYMONGO_SECRET_KEY`, `PAYMONGO_WEBHOOK_SECRET` | `mock` | GCash billing |
| `SMTP_*`, `MAIL_FROM` | console | password-reset emails and receipts |
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY`, `AGENT_PROVIDER` | empty | FEA AI assistant (off without a key) |
| `FREE_MAX_PROJECTS`, `SESSION_DAYS`, `MAX_FAILED_LOGINS`, `LOCKOUT_MINUTES` | 3, 14, 5, 15 | plan and session settings |
| `CFD_DATA_DIR` | `backend/data/cfd` | CFD result files (`runs/<id>/result.npz`, `latest.npz`, solver case folders) |
| `CFD_MAX_CONCURRENT_RUNS` / `CFD_MAX_CELLS` | 2 / 250 000 | CFD run concurrency and case size limit |
| `CFD_OPENFOAM_LAUNCHER` / `CFD_FENICSX_LAUNCHER` / `CFD_PYFR_LAUNCHER` | `auto` | `auto` (PATH → Docker image → WSL), `native`, `docker`, `wsl`, `off` |
| `CFD_OPENFOAM_DOCKER_IMAGE`, `CFD_FENICSX_DOCKER_IMAGE`, `CFD_FENICSX_PYTHON`, `CFD_PYFR_DOCKER_IMAGE`, `CFD_PYFR_DEVICE`, `CFD_*_WSL_DISTRO`, `CFD_OPENFOAM_BUOYANT_SOLVER`, `CFD_MAX_FEM_CELLS`, `CFD_DOCKER_PATH_MAP`, `CFD_DOCKER_AS_ROOT`, `CFD_SCHEDULER_INTERVAL` | see `.env.example` | CFD solver backends (as in CFDFlow) |
| `CFD_MAX_OBSTACLE_TRIANGLES` | 6000 | imported FEA / CAD solids are decimated to this many triangles |
| `CAD_DATA_DIR` | `backend/data/cad` | uploaded / transferred STEP files (`uploads/`) |
| `MAX_FEA_ELEMENTS`, `MAX_TOOLPATH_POINTS`, `CCX_TIMEOUT` | 120 000, 400 000, 1800 | CAD kernel safety limits |
| `CCX_COMMAND`, `CCX_DOCKER_IMAGE`, `CCX_THREADS` | auto, `cadweb/calculix`, 1 | CalculiX for CAD Simulate |
| `VITE_API_URL` (frontend build) | `/api` | base path of the FEA / account client. CFD and CAD use absolute `/api/cfd`, `/api/cad`; set `VITE_API_ORIGIN` only if the API is on another origin |

Settings that moved: CFDFlow's `CFD_DATABASE_URL` and `CFD_CORS_ORIGINS` and CADWeb's `DATABASE_URL` and
`CORS_ORIGINS` are replaced by the platform's `DATABASE_URL` and `ALLOWED_ORIGINS`. CADWeb's `DATA_DIR` became
`CAD_DATA_DIR`. `CFD_FRONTEND_DIST` and `FRONTEND_DIR` (SPA hosting by the API) are not used: nginx or Vite serves the
front end.

---

## 12. API reference (summary)
Interactive docs at `/docs` in development (147 paths). Except for health and the account and billing entry points,
every route needs a signed-in session.

| Area | Endpoints |
|---|---|
| Platform | `GET /api/health` (lists the workspaces), `GET /api/capabilities` |
| Account / billing | `POST /api/auth/register · login · logout · change-password · forgot-password · reset-password`, `GET /api/auth/me`; `GET /api/billing/plans · subscription · payments`, `POST /api/billing/checkout`, `POST /api/webhooks/paymongo` |
| Projects | `GET/POST /api/projects`, `GET/DELETE /api/projects/{id}`, `GET /api/projects/{id}/workspaces` (FEA / CFD / CAD summary), `POST /api/projects/import`, `POST /api/projects/{id}/merge-file`, `GET …/export/project` (v3), `GET …/export/inp` |
| FEA | as in webfea: `…/mesh/*`, `…/material(s)`, `…/parts/*`, `…/bcs`, `…/loads`, `…/constraints`, `…/node-sets`, `…/reference-points`, `…/analysis-type · settings · gravity`, `…/layers/*`, `…/undo · redo · history/{seq}`, `…/solve`, `/api/jobs/{id}`, `…/jobs[/latest · /summary]`, `…/agent/chat` |
| CFD (`/api/cfd`) | `GET templates · solvers`, `POST check`, `GET/POST projects` (`?project_id=`; body `project_id`), `GET/PUT/DELETE projects/{case}`, `POST projects/{case}/duplicate`, `GET projects/{case}/export/{openfoam\|fenicsx\|pyfr}`, `GET/POST projects/{case}/runs`, `GET runs/{id}`, `POST runs/{id}/cancel`, `DELETE runs/{id}`, `GET runs/{id}/monitors · logs · frames · fields · probe-line · export/{vtu\|csv\|monitors}`, `WS runs/{id}/stream` |
| CAD (`/api/cad`) | `GET meta · calculix`; `GET/POST projects`, `POST projects/demo`, `POST projects/{id}/demo`, `GET/PATCH/DELETE projects/{id}`; sketches (CRUD, solve, solve-preview, edit, infer, analysis, render, ops, export, import-dxf); parts (CRUD, features, move, mesh, reference, stabilize-references, export, import-step, duplicate); assemblies (CRUD, components, mates, solve, mesh, interference, bom, export); drawings (CRUD, views, standard-views, dimensions, auto-dimensions, render, export); CAM (`parts/{id}/cam`, `cam/{id}`, auto-operations, generate, simulate, gcode); simulations (`parts/{id}/simulations`, `assemblies/{id}/simulations`, `simulations/{id}`) |
| Transfer (`/api/projects/{id}/bridge`) | `GET sources`, `POST to-fea`, `POST to-cad`, `POST to-cfd`, `POST cfd-to-fea` |

Paths inside `/api/cfd` and `/api/cad` are the original CFDFlow and CADWeb paths with the new prefix, so scripts
written for those APIs only need the prefix and a session cookie.

---

## 13. Testing
```bash
cd backend
pytest tests                       # whole suite, SQLite
pytest tests/cfd tests/cad         # the CFD and CAD suites only
TEST_DATABASE_URL=postgresql://user:pass@host/db_test pytest tests      # same suite on PostgreSQL
CFD_TEST_EXTERNAL=1 pytest tests/cfd   # also runs OpenFOAM / DOLFINx / PyFR for real (Docker images)

cd frontend
npm run typecheck                  # strict TypeScript of the CFD and CAD workspaces
npm test                           # CFD front-end unit tests (vitest)
npm run build
```

| Suite | Tests | What it covers |
|---|---|---|
| Platform + FEA (`tests/*.py`) | 480 | webfea's suite: auth, billing, projects, model stack, every FEA analysis against closed-form results, Gmsh, CAD import, AI agent (scripted LLM) |
| CFD (`tests/cfd`) | 44 (+3 opt-in) | CFDFlow's solver benchmarks (Ghia, Poiseuille, de Vahl Davis, square duct …), backends and fallbacks, API, worker subprocess, WebSocket; plus imported-solid rasterisation (box / sphere parity, OpenFOAM boxes, decimation) |
| CAD (`tests/cad`) | 62 | CADWeb's suite: sketch solver and edits, feature tree and persistent references, mates, drawings, CAM, CalculiX (skipped when `ccx` is not installed), API |
| Integration (`test_suite_integration.py`) | 11 | three workspaces per project, ownership across all APIs (404 for other users, 401 anonymous), plan limit, cascading delete, v3 file round trip with id remapping and merge, v2 files, every Transfer direction (exact CAD volumes from FEA primitives, CAD material → FEA, a real CFD run whose pressure maps back onto the FEA model) |
| Front end | 6 + type check + build | rasterisation parity, colour maps, streamlines |

Last full run: **591 passed, 9 skipped** (600 backend tests, about 5 minutes on SQLite) and 6/6 front-end tests.

The CFD / CAD suites run through `tests/conftest.py::workspace_client`, which signs in a Pro user and sends their
original paths to `/api/cfd` / `/api/cad`. On a machine without CalculiX and the external CFD solvers, 9 tests are
skipped, as in the original projects. The UI was also driven end to end in headless Microsoft Edge: FEA, the
switcher, Transfer FEA → CFD, the CFD setup canvas with the imported body, a CFD run with live WebSocket streaming and
results, the CAD demo, sketch and model, Transfer CAD → FEA, and light and dark themes. No front-end errors were
logged beyond the expected 401 (before login) and 404 (no FEA run yet) responses.

---

## 14. What was kept from each project
- **webfea**: all of it. Website, accounts, billing, the FEA engine and every endpoint, the model stack, undo, AI
  assistant, Guide, ribbon and dialogs, project files v1 / v2, `.inp` export, Jenkins CI/CD, nginx. Changed: the
  Functions switcher is now FEA / CFD / CAD; the former CAD / CAM / CAE FEA sub-workspaces are merged into the FEA
  ribbon (the CAM *Setup* is the **Stock** command); project files are version 3.
- **cfdflow**: the whole backend (case schema, 11 examples + blank case, case check, run scheduler and worker processes, native
  2-D / 3-D solvers, OpenFOAM / FEniCSx / PyFR backends with fallbacks, results, exports, WebSocket) and front end
  (cases, setup, runs, results). Changed: routes under `/api/cfd`; cases belong to Gearhead projects; the case list
  shows the open project's cases; theme and logo come from the shell. New: the `mesh` obstacle shape.
- **cadweb**: the whole backend (sketch, modelling, toponaming, assemblies, mate solver, drawings, CAM, posts,
  simulation, CalculiX, demo) and front end (six workspaces). Changed: routes under `/api/cad`; one CAD project per
  Gearhead project; the six tabs moved into the ribbon; *Load demo* adds the demo to the open project. New:
  `POST /api/cad/projects/{id}/demo`.

The original folders (`../webfea`, `../cfdflow`, `../cadweb`) are untouched. Data in the stand-alone CFDFlow / CADWeb
databases is not migrated automatically, because those apps had no users to assign it to. Recreate it, or import it
into a project through their APIs (`/api/cfd/projects`, `/api/cad/...`).

---

## 15. Deployment
### 15.1 Docker Compose
`docker compose up -d --build` starts PostgreSQL, the backend (one uvicorn worker, not published) and nginx (the SPA +
`/api` proxy, with the CFD WebSocket upgraded at `/api/cfd/runs/*/stream`). Request bodies up to 60 MB (project files
with embedded STEP files) and 5-minute timeouts (meshing CAD, transfers) are allowed. The database keeps the name
`webfea`, so an existing Gearhead FEA deployment upgrades in place: the new `cfd_` / `cad_` tables are created at
start-up. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/DEPLOY_NGINX.md](docs/DEPLOY_NGINX.md) and
[docs/CI_CD_JENKINS.md](docs/CI_CD_JENKINS.md); the Jenkins pipeline works unchanged and now also runs the CFD and CAD
tests.

**Run one backend worker.** The CFD scheduler is an in-process thread, CAD simulations are background tasks, and Gmsh
keeps global state. CFD parallelism comes from the worker processes (`CFD_MAX_CONCURRENT_RUNS`).

### 15.2 External CFD solvers in Docker
```bash
# solver images (≈ 6 GB in total)
docker pull opencfd/openfoam-default:2406
docker pull dolfinx/dolfinx:v0.9.0
docker build -t cfdflow/pyfr:2.0.3 docker/pyfr
# backend with the Docker CLI, the Docker socket and a host-mounted run directory
CFD_HOST_DATA_DIR=$PWD/cfd-runs docker compose -f docker-compose.yml -f docker-compose.solvers.yml up -d --build
```
The overlay installs the Docker CLI in the backend image, mounts the Docker socket and bind-mounts the CFD run
directory at the same host path that the solver containers mount. Use it only on trusted hosts. Without it, the CFD
workspace runs the Python equivalents and says so in the case check.

### 15.3 CalculiX
The backend image installs `calculix-ccx`. Locally, install `ccx`, or build `docker/calculix`
(`docker build -t cadweb/calculix docker/calculix`). CAD *Simulate* shows whether it was found.

---

## 16. Troubleshooting
- **A workspace shows "Select or create a project"**: open **Project ▾** and pick or create one. All three workspaces
  work on the open project.
- **CFD runs stay "queued"**: the scheduler starts with the app's lifespan. Run uvicorn normally (not several workers).
  `CFD_MAX_CONCURRENT_RUNS` limits parallel runs.
- **The live CFD monitor does not update behind a proxy**: the proxy must upgrade WebSockets for
  `/api/cfd/runs/{id}/stream` (see `frontend/nginx.conf`; the Vite dev proxy has `ws: true`).
- **"No surface face of the FEA model lies next to fluid"** (CFD loads): make the FEA model that the obstacle came from
  the active model (Model panel). In 2-D cases, faces away from the centre section and end caps get no load.
- **Transfer → CFD: "covered by only n cells"**: refine the case mesh in Setup → Mesh, or send a 2-D section.
- **"The surface is not closed, so no solid could be built"** (→ CAD): the FEA part's surface has gaps (an imported
  surface mesh). Use a solid mesh, or send the original CAD file through CAD → *STEP* import.
- **Transfer → FEA fails meshing**: set an element size (mm) instead of automatic, or use Tet elements. Bricks need
  sweepable shapes.
- **Mock checkout refused**: `PAYMENT_PROVIDER` is `paymongo` in your `.env`. Use `mock` for local development.
- **CAD shows "Cannot reach the API"**: check that the backend has cadquery (`pip install -r requirements.txt`) and the
  OpenCascade shared libraries listed in [1.2](#12-local-development).
- FEA-specific issues (constraints, buckling, convergence, Gmsh options, the AI assistant): see the troubleshooting
  section of [docs/WORKSPACE_RIBBON_AND_MODEL_STACK.md](docs/WORKSPACE_RIBBON_AND_MODEL_STACK.md) and the other FEA docs.

---

## 17. Limitations
- **Transfer is one-way at a time.** Bodies in CFD and parts in CAD are copies. Editing a CAD part does not update an
  FEA mesh or CFD obstacle made from it: send it again.
- **CFD obstacles** are staircase bodies on a uniform Cartesian mesh (all backends). Imported solids are capped at
  `CFD_MAX_OBSTACLE_TRIANGLES` (vertex-clustering decimation). Turbulence needs OpenFOAM. Compressible and multiphase
  flow are out of scope (as in CFDFlow).
- **CFD → FEA loads** use the cell-centred pressure next to each face (no wall shear), grouped into levels. In 2-D,
  the section's pressure is applied along the whole body length (the 2-D assumption).
- **FEA → CAD** keeps parametric history only for primitives. Other parts become dumb solids sewn from their surface
  triangles (faceted). Beam, truss, shell and spring models are not solids and are not transferred.
- **Files** carry definitions, not results. CFD and CAD have no undo history (FEA does).
- The two FEA solvers (FEA workspace engine; CAD *Simulate* with scikit-fem / CalculiX) are separate tools that model
  CAD geometry differently. Use FEA for the full PrePoMax-style workflow, and CAD *Simulate* for quick checks on
  B-rep faces and for CalculiX contact.
- The remaining limitations of each tool are listed in [docs/cfd/CFDFLOW_REFERENCE.md](docs/cfd/CFDFLOW_REFERENCE.md)
  (*Limitations and roadmap*), [docs/cad/CADWEB_REFERENCE.md](docs/cad/CADWEB_REFERENCE.md) (*Limitations*) and the
  FEA docs.

---

## 18. Documentation map
| Doc | Covers |
|---|---|
| this README | the integrated suite: workspaces, project files, Transfer, architecture, configuration, API, tests, deployment |
| [docs/cfd/CFDFLOW_REFERENCE.md](docs/cfd/CFDFLOW_REFERENCE.md) | CFD in depth: solver method, 3-D, OpenFOAM / FEniCSx / PyFR backends, validation tables, case definition (paths there are the stand-alone ones; prefix `/api/cfd`) |
| [docs/cad/CADWEB_REFERENCE.md](docs/cad/CADWEB_REFERENCE.md) | CAD / CAM in depth: kernels, persistent references, mates, drawings, CAM, CalculiX, validation (prefix `/api/cad`) |
| [docs/WORKSPACE_RIBBON_AND_MODEL_STACK.md](docs/WORKSPACE_RIBBON_AND_MODEL_STACK.md) | FEA ribbon, dialogs, left panel, model stack (its CAD / CAM / CAE switcher section is superseded by §2 above) |
| [docs/SOLVER_REFERENCE.md](docs/SOLVER_REFERENCE.md) and the other FEA docs | FEA solver, meshing, parts, contact, NLGEOM, AI assistant, Guide |
| [docs/AUTH_AND_BILLING.md](docs/AUTH_AND_BILLING.md), [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) | accounts, billing, visual identity |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/DEPLOY_NGINX.md](docs/DEPLOY_NGINX.md), [docs/CI_CD_JENKINS.md](docs/CI_CD_JENKINS.md) | operations |

## 19. License and attribution
Provided as-is. Backend: FastAPI, SQLAlchemy, Pydantic, NumPy, SciPy, Argon2, psycopg, httpx, **Gmsh** (GPL; runs
in-process and in a subprocess), **cadquery / OpenCascade**, scikit-fem, shapely, ezdxf, meshio, foamlib. Optional
external programs: **CalculiX**, **OpenFOAM**, **FEniCSx / DOLFINx**, **PyFR** (run as separate processes or
containers). The FEA AI assistant uses Google Gemini (or Anthropic / OpenRouter). Frontend: React, React Router, Vite,
Three.js, `@react-three/fiber` / `drei`, Fontsource fonts. Gmsh's GPL does not extend to a web service, but shipping
the app as a closed-source desktop installer would be distribution; check Gmsh's licensing first.
 