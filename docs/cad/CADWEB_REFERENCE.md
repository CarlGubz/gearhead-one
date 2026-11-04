# Gearhead CAD — browser-based CAD / CAM / CAE

A full-stack web application that combines, in one place, the main workflows of AutoCAD (2D drafting and drawing sheets) and
Fusion 360 / SolidWorks (parametric 3D modelling, assemblies, CAM, simulation). It shares the look of Gearhead FEA
(`../webfea`): the same colours, fonts, light ("vellum") and dark ("blueprint") themes, and hex-nut logo, with **CAD** in the badge.

| Layer | Technology |
|---|---|
| Backend | **Python 3.12+ / FastAPI**, SQLAlchemy 2 |
| Database | **PostgreSQL 16** (JSONB for parametric definitions); SQLite for tests |
| Frontend | **TypeScript**, React 18, Three.js, Vite |
| CAD kernel | **CadQuery / OpenCascade** (`cadquery`, OCP): modelling, hidden-line removal for drawings |
| 2D geometry | `shapely`, `ezdxf`, `scipy` (constraint solver) |
| FEA | `gmsh` (meshing) + `scikit-fem` (native linear solvers) + **CalculiX** (nonlinear, transient, buckling, contact) |
| CAM | own toolpath engine on `numpy`/`scipy`/`shapely`, own post-processors |

> **Honest scope statement.** This is a compact, working engine that covers every area asked for
> (2D, 3D, assembly, drawings, CAM, simulation) with real, tested algorithms. It is not a clone of commercial products
> that took thousands of person-years each. See [Limitations](#8-limitations) for what is deliberately not included.

---

## 1. Quick start

### Docker (recommended)

```bash
docker compose up --build
```

* Web app: <http://localhost:8080>
* API + interactive docs: <http://localhost:8000/docs>

Click **Load demo** in the top bar for a ready-made project (a parametric plate, two pins, mates, and a 5-operation CAM setup).
The backend image includes CalculiX (`calculix-ccx`), so the nonlinear and contact analyses work out of the box.

### Local development

```bash
# 1. PostgreSQL
docker run -d --name cadweb-db -p 5432:5432 -e POSTGRES_USER=cad -e POSTGRES_PASSWORD=cad -e POSTGRES_DB=cadweb postgres:16-alpine

# 2. Backend (Python 3.12 or 3.13)
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000              # DATABASE_URL defaults to the container above

# 3. Frontend
cd ../frontend
npm install
npm run dev                                            # http://localhost:5173 (proxies /api to :8000)

# 4. (optional) CalculiX for the nonlinear / buckling / transient / contact analyses
docker build -t cadweb/calculix ../docker/calculix     # or install ccx yourself, see 2.6
```

Notes:

* **Port 5432 already in use?** If another PostgreSQL (for example a native Windows service) listens on 5432, the backend
  connects to the wrong server and fails with `password authentication failed for user "cad"`. The UI then shows
  *500 Internal Server Error* for `/api/meta`. Map the container to another port (`-p 5433:5432`) and set
  `DATABASE_URL=postgresql+psycopg2://cad:cad@localhost:5433/cadweb` in `backend/.env`.
* Linux needs a few shared libraries for OpenCascade/gmsh (`libgl1 libglu1-mesa libxft2 libxrender1 libxcursor1 libxinerama1`).
* **Schema updates:** tables are created at start-up, and columns added in newer versions (for example sketch layers, simulation
  jobs on assemblies) are added to an existing database automatically. This is a small forward-only migration step in `main.py`.

Configuration (environment variables or `backend/.env`):

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | SQLAlchemy URL (default `postgresql+psycopg2://cad:cad@localhost:5432/cadweb`) |
| `CORS_ORIGINS` | comma-separated allowed origins |
| `DATA_DIR` | uploads (STEP files) |
| `MAX_FEA_ELEMENTS`, `MAX_TOOLPATH_POINTS` | safety limits for meshing / toolpaths |
| `CCX_COMMAND` | full command to run CalculiX (`{dir}` = job directory); overrides auto-detection |
| `CCX_DOCKER_IMAGE` | CalculiX image used when `ccx` is not installed (default `cadweb/calculix`) |
| `CCX_THREADS` | CalculiX solver threads (default `1`, see 2.6) |
| `CCX_TIMEOUT` | seconds per CalculiX run (default 1800) |
| `FRONTEND_DIR` | `/path/to/frontend/dist`: the API also serves the built UI (single-process deployment) |

### Tests

```bash
cd backend
pytest -q                                     # SQLite, 62 tests (CalculiX tests are skipped when ccx is not available)
TEST_DATABASE_URL=postgresql+psycopg2://cad:cad@localhost:5432/cadweb_test pytest -q   # PostgreSQL
cd ../frontend && npm run build               # strict TypeScript check + production bundle
```

---

## 2. Features

The top bar has six workspaces: **Sketch 2D · Model 3D · Assembly · Drawing · CAM · Simulate**, plus a light/dark theme toggle.

### 2.1 2D sketching (AutoCAD-style drafting)
* Entities: line, rectangle, circle, arc, polyline, polygon, slot, ellipse, spline, point; construction geometry.
* Snap to grid and to end/centre points, pan/zoom, live preview while drawing. The line tool locks to horizontal / vertical within 3°.
* **Modify tools:** **trim** (cut a line / arc / circle between its intersections), **extend** (to the next boundary),
  **fillet** (click a corner where two lines meet, rect / polyline corners included; adds tangency and radius constraints)
  and **explode** (rect / polyline / polygon / slot into lines and arcs).
* **Geometric constraint solver** (SciPy least-squares): horizontal, vertical, coincident, distance, horizontal/vertical distance,
  fixed, radius, diameter, parallel, perpendicular, equal, angle, tangent, concentric, point-on-line, point-on-circle, midpoint, symmetric.
  Reports `fully_constrained / solved / unconstrained / conflict` and the remaining **degrees of freedom** (SVD rank of the Jacobian).
* **Constraint inference:** while drawing, new geometry is constrained automatically (horizontal / vertical, coincident end points,
  point on line / circle, line tangent to an arc). **Auto-constrain** does the same for a whole sketch, for example after a DXF import.
* **Layers:** colour, visibility and lock per layer, a current layer for new geometry, and moving the selection between layers.
  Layer names and colours are kept through DXF import / export.
* Transform tools: translate, rotate, scale, mirror, linear & circular patterns, offset (copy or move).
* Profile analysis (closed loops, open chains, area, perimeter) via shapely.
* DXF import / export, SVG export (`ezdxf`).

### 2.2 3D parametric modelling (SolidWorks/Fusion-style feature tree)
* Primitives: box, cylinder, sphere, cone.
* Sketch features: **extrude** (normal / reverse / symmetric, through-all, taper, add/cut/intersect), **revolve**, **sweep**, **loft**.
* Dress-up and body features: **hole** (simple / counterbore / countersink), **fillet**, **chamfer**, **shell**, linear / circular **patterns**, **mirror**, transform, boolean **combine**, **STEP import**.
* The feature tree is stored as JSON and **replayed** by the kernel. Each feature reports its own status, so a failing feature is flagged without losing the model.
  Features can be reordered, suppressed, renamed, deleted and edited at any time (history-based).
* **Named parameters and expressions**: any numeric field accepts `=expr`, e.g. `=hole_d*2 + 3` (evaluated server-side by a restricted arithmetic AST evaluator, no `eval`).
* Face / edge selection by CadQuery selectors (`>Z`, `|X`…), `idx:N`, `near:x,y,z`, `faceedges:<selector>`, and **persistent references**:
  * While the tree is replayed, every face gets a **provenance tag** (the feature that created it). A `ref:` selector stores
    that tag plus a geometric signature (type, centre, normal, area, radius).
  * References survive upstream edits that renumber faces (inserted features, changed dimensions, edited sketches).
    If the original face is gone, the best geometric match is used and the feature shows a warning. If nothing matches, it reports *reference lost*.
  * Clicking a face in the viewport creates a face reference and a reference to the nearest edge (for fillet / chamfer).
    **Make persistent** rewrites old `idx:` / `near:` selectors in a part.
* Mass properties (volume, area, mass from material density, centre of mass, bounding box), 10 built-in materials.
* Export STEP, STL, BREP.

### 2.3 Assemblies
* Insert any number of part instances (4×4 poses), fixed/grounded components.
* **Mates** (faces picked in the viewport and stored as persistent references): *flush*, *distance*, *angle*, *parallel*,
  *perpendicular*, *concentric*, *tangent* (plane–cylinder, plane–sphere, cylinder–cylinder), *point* and *lock*.
  Planes, cylinders, cones and spheres can be mated.
* **Simultaneous solver:** all mates are solved together in one least-squares problem. The result is *fully constrained*,
  *under-constrained (n DOF free)* or *conflict*, with a status per mate. Free directions stay where they were.
* **Interference detection** (boolean intersection volume and location), **bill of materials** (quantity, mass), exploded view slider, STEP assembly export.

### 2.4 Drawing sheets (AutoCAD / SolidWorks drawings)
* **Sheets:** A4–A0, Letter, Tabloid; border and title block (company, title, drawing number, material, scale, units, drawn by,
  date, tolerance, projection, revision); notes.
* **Views:** front / top / right / left / back / bottom and isometric, created by OpenCascade hidden-line removal.
  Visible edges are thick, tangent edges thin, hidden edges dashed, and holes get centre marks.
  **Standard views** lays out front + top + right + iso in **3rd- or 1st-angle** projection at an ISO 5455 scale that fits the sheet.
  Views can be dragged, rescaled and relabelled.
* **Dimensions:** horizontal, vertical, aligned, radius, diameter, with an optional ± tolerance or text override.
  Click two snap points (or a circle centre), then place the dimension line.
  * **Associative:** dimension anchors are persistent edge references, so values are re-measured from the current model on every render.
  * **Auto-dimension** adds overall sizes per view and one dimension per hole / arc size with a count (`2x Ø10`, `8x R4`).
* **Export:** vector **PDF** (built-in writer, no extra dependency) and SVG.

### 2.5 CAM (Fusion-style 2.5D / 3-axis)
* Stock definition (margins or explicit box). The tool library (flat, ball, drill) has feeds/speeds and a **holder envelope**:
  flute length, stickout, shank Ø, holder Ø.
* Operations:
  * **face mill**
  * **contour/profile** (inside/outside, multiple stepdowns)
  * **pocket / area clear** (offset-ring clearing, helical ramps, climb/conventional)
  * **drill / hole milling** (canned cycles)
  * **3D roughing (waterline)** with stock-shadow accumulation
  * **3D finishing (parallel raster)** with a drop-cutter model for flat and ball tools
* **Rest machining** (`rest` on profile / pocket / waterline / raster): the stock left by earlier operations is tracked on a height map.
  The toolpath is clipped to where this tool would still remove more than `rest_threshold`, for example corners a large cutter could not reach.
* **Automatic feature recognition** proposes face / pocket / drill / profile operations from the B-rep (hole detection, planar floors).
  Holes through a pocket floor are cleared with the pocket, not left as islands.
* A linker chooses retract-vs-feed links; 3D toolpath viewer with rapid / cut / plunge colouring and a playback slider.
* **Collision checking:** after linking, the program is replayed on an in-process stock model. It reports **shank** and **holder**
  contact with stock or part and **rapids through material**, with location, depth and operation, and marks them in the viewport.
* **Material-removal simulation** (height-map / dexel) reporting removed volume, **leftover** and **gouged** volume, with a colour-coded error view.
* **G-code post-processors**: Fanuc, LinuxCNC, GRBL (canned cycles, tool changes, work offset, line numbers, coolant).

### 2.6 Simulation (finite-element analysis)
Analyse a **part**, or an **assembly** (contact).

**Native solver** (`services/simulation.py`, pure Python):
* **Linear static** (supports, face forces, pressures, gravity), **steady-state thermal** (fixed temperature, heat flux, convection),
  **modal** (natural frequencies + animated mode shapes).
* Pipeline: B-rep → `gmsh` tetrahedral mesh → `scikit-fem` quadratic (P2) tetrahedra for elasticity, P1 for conduction → sparse direct / eigen solvers.

**CalculiX adapter** (`services/calculix.py`; CalculiX runs as a separate program, quadratic C3D10 tetrahedra):

| Analysis | What it adds |
|---|---|
| Nonlinear static | bilinear **plasticity** (yield from the material, tangent modulus), **large deformation** (NLGEOM), load increments |
| Buckling | linear eigenvalue buckling: load factors + mode shapes |
| Natural frequencies | cross-check of the native modal solver |
| Transient thermal | heat-up / cool-down from an initial temperature, with time history |
| Transient dynamic | implicit dynamics, step or ramped load, displacement history, peak response |
| **Contact (assembly)** | frictional or bonded contact between components; touching planar / cylindrical faces are paired automatically |

Common to both solvers:
* Boundary conditions are attached to B-rep faces through the same selectors as the modeller (viewport picks are persistent
  references), so setups survive remeshing and model edits. Assembly boundary conditions name the component.
* Results: von Mises stress, displacement, temperature, plastic strain, contact pressure on the surface with a colour legend.
  Also a deformation scale slider, safety factor, equilibrium check, and time-history charts.
* Jobs run as background tasks, are stored in the database and polled by the UI.

**Finding CalculiX:** `CCX_COMMAND` first, then `ccx` on `PATH` (Debian/Ubuntu `apt install calculix-ccx`; FreeCAD ships `ccx.exe`
on Windows), then the docker image `cadweb/calculix` (`docker build -t cadweb/calculix docker/calculix`). The Simulate workspace
shows whether it was found.

**One thread by default (`CCX_THREADS=1`):** the multithreaded SPOOLES build in Debian / Ubuntu returns spurious buckling
eigenvalues that change from run to run. Single-threaded results are repeatable and match theory.

---

## 3. Architecture

```
frontend/ (React + TS + Three.js)  ──HTTP/JSON──▶  backend/ (FastAPI)  ──SQLAlchemy──▶  PostgreSQL
                                                     │
                                                     ├─ services/sketch2d.py      2D kernel, constraint solver, layers, DXF/SVG
                                                     ├─ services/sketch_edit.py   trim / extend / fillet / explode, constraint inference
                                                     ├─ services/modeling3d.py    feature-tree replay on CadQuery/OCC
                                                     ├─ services/toponaming.py    face provenance + persistent ref: selectors
                                                     ├─ services/assembly.py      poses, interference, BOM, STEP export
                                                     ├─ services/mate_solver.py   simultaneous mate solver (9 mate types)
                                                     ├─ services/drawing.py       HLR views, dimensions, title block, SVG / PDF
                                                     ├─ services/geometry_mesh.py triangle-mesh slicing / heightfields
                                                     ├─ services/cam.py           toolpaths, rest machining, collisions, removal simulation
                                                     ├─ services/gcode.py         post-processors
                                                     ├─ services/simulation.py    gmsh + scikit-fem FEA (native)
                                                     ├─ services/calculix.py      CalculiX adapter: deck writer, runner, result parsers
                                                     └─ routers/                  REST API
                                                                    │
                                                                    └──▶ ccx (local binary or docker/calculix image)
```

**Design decision: store definitions, not geometry.** The database holds sketches (entities, constraints, layers), ordered
feature lists, assembly poses/mates, drawings (sheet, views, dimension anchors), CAM setups and simulation jobs as JSONB.
B-reps are rebuilt on demand, which keeps the database small, makes every model fully editable, and avoids binary-format lock-in.
Heavy outputs (meshes, projected views, toolpaths, results) are computed per request or per job.

Database tables: `projects`, `sketches`, `parts`, `assemblies`, `assembly_components`, `mates`, `drawings`, `cam_setups`, `simulation_jobs`.

Units: millimetres, degrees, Z-up everywhere. Both FE solvers use a consistent N–mm–tonne–s system internally.

### REST API (all under `/api`, full OpenAPI at `/docs`)

| Area | Endpoints |
|---|---|
| Meta | `GET /meta` (schemas for features, CAM ops, materials, mates, CalculiX status…), `GET /health`, `GET /calculix` |
| Projects | `GET/POST /projects`, `POST /projects/demo`, `GET/DELETE /projects/{id}` |
| Sketches | CRUD, `POST /sketches/{id}/solve`, `POST /solve-preview`, `POST …/edit` (trim/extend/fillet/explode), `POST /sketch-infer`, `POST …/infer`, `GET …/analysis`, `GET …/render`, `POST …/ops`, `GET …/export?format=dxf\|svg`, `POST /projects/{id}/sketches/import-dxf` |
| Parts | CRUD, `POST …/features`, `PATCH/DELETE …/features/{fid}`, `POST …/features/{fid}/move`, `GET …/mesh`, `GET …/reference?kind=face\|edge&indices=…`, `POST …/stabilize-references`, `GET …/export?format=step\|stl\|brep`, `POST …/parts/import-step`, `POST …/duplicate` |
| Assemblies | CRUD, components, mates, `POST …/solve` (status, DOF, per-mate status), `GET …/mesh`, `GET …/interference`, `GET …/bom`, `GET …/export` |
| Drawings | `GET/POST /projects/{id}/drawings`, `GET/PATCH/DELETE /drawings/{id}`, `POST …/views`, `POST …/standard-views`, `POST …/dimensions`, `POST …/auto-dimensions`, `GET …/render`, `GET …/export?format=pdf\|svg` |
| CAM | `GET/POST /parts/{id}/cam`, `GET/PATCH/DELETE /cam/{id}`, `POST …/auto-operations`, `POST …/generate` (incl. collisions), `POST …/simulate`, `GET …/gcode` |
| Simulation | `POST /parts/{id}/simulations` and `POST /assemblies/{id}/simulations` (202, background), `GET /simulations/{id}`, `GET /parts/{id}/simulations`, `GET /assemblies/{id}/simulations`, `DELETE /simulations/{id}` |

---

## 4. Simulation / geometry packages and why they were chosen

| Package | Role | Why |
|---|---|---|
| **CadQuery 2.x (OpenCascade)** | B-rep modelling, booleans, fillets, STEP/STL, hidden-line removal | Pure `pip install`, the production-grade OCC kernel behind FreeCAD, with a Pythonic API |
| **gmsh** | Tetrahedral meshing (linear and quadratic) | Robust OCC-geometry meshing, pip wheel with Python API |
| **scikit-fem** | Native FEM assembly | Pure Python/NumPy, no compiled toolchain, supports P2 tets and facet integrals |
| **CalculiX** | Nonlinear, transient, buckling and contact FEA | Mature open-source Abaqus-style solver. It runs as a separate process (its GPL does not extend to this app) and is optional |
| **SciPy** | Sparse solvers, eigen-solver, least-squares (sketch and mate solvers) | Standard, battle-tested |
| **shapely** | 2D booleans, offsets, regions | Clean API for toolpath offsetting and profile nesting |
| **ezdxf** | DXF I/O | De-facto standard Python DXF library |

FEniCS, OpenFOAM and FreeCAD were considered but need heavier system installs. CalculiX is kept optional (docker image or
local binary), so the core app still installs as a single `pip` environment.

**Licensing note:** gmsh is GPL and runs inside the backend process. Running the app as a web service is not distribution,
but shipping it as a closed-source desktop installer would be. See gmsh's licensing options before doing that.

---

## 5. Validation

Automated tests (**62**) cover:
* the sketch solver, trim / extend / fillet, constraint inference and layers;
* the feature tree and persistent references (upstream inserts, resizes, sketch edits, lost and re-attached references);
* the simultaneous mate solver (DOF, conflicts, tangent, lock);
* drawings (projection, layout, associative dimensions, SVG / PDF);
* CAM (rest machining, holder / rapid collisions, a no-false-collision check on the demo program);
* CalculiX against beam theory, and the full HTTP API.

They pass on SQLite and on PostgreSQL 16.

FEA checks against closed-form solutions (cantilever 100×10×10 mm steel bar, 100 N tip load):

| Quantity | Analytical | Native (P2, 2.5 mm) | CalculiX (C3D10) |
|---|---|---|---|
| Tip deflection | 0.200 mm (PL³/3EI) | **0.2006 mm** | **0.2006 mm** |
| First bending frequency | 815.3 Hz (Euler–Bernoulli) | **813.0 Hz** | **812.4 Hz** |
| Peak von Mises at the root | 60 MPa (σ = Mc/I) | 50.2 MPa (see note) | **60.1 MPa** |
| Buckling, 1 kN compression (cantilever) | factor 41.1 (π²EI/4L²) | – | **40.98** |
| Global equilibrium (reaction vs load) | 0 | 1.7·10⁻⁹ N | < 10⁻⁹ N |

Further CalculiX checks:
* Plasticity: a 500 N tip load yields the bar, and von Mises is capped near yield.
* Contact: a block pressed onto a base at 50 MPa gives a support reaction of 20 000 N = 50 MPa × 400 mm².
* Transient thermal: the mean temperature rises monotonically towards the boundary temperature.

*Note:* the native solver reports a volume-weighted nodal average recovered from element data, which smooths the singular
stress concentration at the fixed face. Use CalculiX, or refine the mesh, when the peak matters.

An end-to-end run against a live backend and PostgreSQL covered: demo project load, reference upgrade, simultaneous mate solve,
CAM generation with collision check, a drawing with auto-dimensions exported to PDF, and a CalculiX plasticity job.
The original workspaces were also exercised in a headless browser. The new UI panels (sketch modify tools, layers, Drawing tab,
CalculiX options) are type-checked and built, but have not been click-tested in a browser.

---

## 6. Using the app (5-minute tour)

1. **Load demo** → *Model 3D*: click features in the tree, change the named parameters (`t`, `hole_d`, `pocket_depth`) and watch the model rebuild.
   Click **Make persistent** to turn the demo's point selectors into persistent references.
2. *Sketch 2D*: pick *Plate outline* and see the constraints that drive it. Draw lines (they get horizontal / coincident
   constraints automatically), then use **Fillet** on a corner and **Trim** on an overlap. Add a layer and move geometry onto it.
3. *Assembly*: **Solve mates** (all solved together, DOF shown), then **Interference** and **BOM**; use the Explode slider.
   Try a *distance* or *angle* mate.
4. *Drawing*: **＋ From part** on the mounting plate gives front / top / right / iso views. Click **Auto-dimension**,
   add a dimension with the ↔ tool, fill in the title block, then **⇩ PDF**. Change `t` in *Model 3D* and the dimensions update.
5. *CAM*: **Generate toolpaths** (collisions are listed if any), scrub the playback, **Simulate removal**, download the `.nc` file.
   Add a small end mill and a pocket with *rest machining* to clean the corners.
6. *Simulate*: add a *Fixed support* on `<Z` and a *Pressure* on `>Z`, then **Run analysis**. Switch to *Nonlinear static* with plasticity,
   *Buckling*, or **Assembly (contact)** when CalculiX is available.

Tip: in Model / Assembly / Simulate, click a face in the viewport, then press **use face** / **use edge** / **pick** in a selector field.
The pick is stored as a persistent reference (📌).

---

## 7. Project layout

```
cadweb/
├── docker-compose.yml  .env.example
├── docker/calculix/Dockerfile        CalculiX solver image (optional, used when ccx is not installed)
├── backend/
│   ├── Dockerfile  requirements.txt
│   ├── app/{main,config,database,models,schemas}.py
│   ├── app/routers/    meta projects sketches parts assemblies drawings cam simulations
│   ├── app/services/   sketch2d sketch_edit modeling3d toponaming assembly mate_solver drawing
│   │                   geometry_mesh cam gcode simulation calculix store demo
│   └── tests/
└── frontend/
    ├── Dockerfile  nginx.conf  package.json  vite.config.ts  tsconfig.json  public/favicon.svg
    └── src/{App,main,store,api,types}.ts(x)  styles.css
        ├── brand/       Logo.tsx brand.css tokens.css   (Gearhead design tokens shared with webfea)
        ├── components/  Viewer.ts Viewport.tsx ParamForm.tsx ui.tsx
        └── workspaces/  Sketch Model Assembly Drawing Cam Simulate
```

---

## 8. Limitations

What a project of this size does **not** do:

* **No multi-user auth / permissions, no version history or collaboration.** Add an auth layer before exposing it publicly; simulation and CAM jobs are CPU-heavy.
* **Sketch editing:** no dragging of constrained geometry (the solve happens after each change). Trim / extend work on lines, arcs and
  circles (compound entities are exploded first); fillet works on two lines. No undo/redo.
* **Modelling:** no surfacing (NURBS patches), sheet metal, weldments or draft analysis. Persistent references cover most
  renumbering cases but are not a complete topological-naming solution. When a referenced face is split or merged by an
  upstream change, the best geometric match is used and the feature shows a warning.
* **Assemblies:** no kinematic motion study, no gear/cam mates, no assembly-level patterns.
* **Drawings:** no section / detail / auxiliary views, GD&T frames, BOM tables or multi-sheet drawings. Dimensions are linear, radius
  and diameter only (no angle, ordinate or chain dimensions). Linear anchors are edge end points or circle centres.
* **CAM:** 3-axis only (no 4/5-axis, turning or multi-setup fixturing). No Z-level / scallop finishing, trochoidal / adaptive
  clearing or tool-wear models. Rest machining, simulation and collision checks run on a top-down height map (no undercuts).
  Cycle times are feed-based estimates. Posts are generic, so **always verify G-code in a simulator before running it on a real machine.**
* **Simulation:** the native solver is linear, isotropic, small-strain. CalculiX adds plasticity (bilinear), large deformation, buckling,
  transient and contact, but there is no CFD. Contact jobs are slow (unsymmetric solve, single-threaded), and every component needs
  enough support not to float. Contact pairs are found only between coincident planar and cylindrical faces (other pairs: `contact_pairs` in the config).
  Jobs run in-process (FastAPI background tasks); for production use a real queue (Celery/RQ/Arq).
* **Docker images** were authored against the pinned dependency versions. The app was run and tested natively (PostgreSQL 16),
  and only the `docker/calculix` image has been built here, so expect to adjust apt packages if a base image changes.

## 9. Possible next steps
Undo/redo, constraint dragging, motion studies, section and detail views, angle dimensions, Z-level and scallop finishing,
adaptive clearing, authentication, a job queue, and a desktop build (pywebview / Electron with a bundled backend and SQLite).
