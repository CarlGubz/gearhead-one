# Solver & PrePoMax Parity — Implementation Log

Companion to [`SOLVER_AND_PREPOMAX_PLAN.md`](SOLVER_AND_PREPOMAX_PLAN.md). Technical details: [`SOLVER_REFERENCE.md`](SOLVER_REFERENCE.md).

## 1. What was done

### Solver (task 2) — CalculiX removed, standalone engine added
- **Removed:** `calculix.py`, the old `solver.py`, the `ccx` detection in `/api/health`, the `calculix-ccx` apt step in the backend `Dockerfile`, and every UI/marketing reference. The system no longer depends on any external solver.
- **Added `backend/app/fea/`** (NumPy/SciPy only): `elements.py`, `model.py`, `materials.py`, `assembly.py`, `constraints.py`, `analyses.py`, `recovery.py`, `meshgen.py`, `inp.py`, `runner.py`.
- **Vectorised assembly** (batched `einsum`) replaces the per-element Python loops (a ~19k-DOF tet model assembles + solves in ~1 s).
- **Elements:** C3D4, C3D10, C3D8, C3D20 (CalculiX node order).
- **Materials:** + thermal expansion, **J2 plasticity** with consistent tangent.
- **Analyses (8):** static, nonlinear (plastic, incremental Newton), frequency, linear buckling, implicit dynamics (Newmark + Rayleigh damping + amplitudes), steady heat (conduction, convection, radiation, heat sources), transient heat (θ-method), sequential thermo-mechanical.
- **Loads:** force, pressure, traction vector, remote force/moment on a reference point, gravity, **centrifugal**, uniform/thermal-field strain, nodal/surface/volumetric heat, convection film, radiation.
- **Constraints:** reference points, **rigid body**, **tie** (node-to-face interpolation); symmetry & prescribed-displacement supports.
- **Robustness:** singular/under-constrained systems are detected and explained; buckling refuses tension-only loads instantly (an early version stalled in ARPACK — found by the browser test and fixed with a Rayleigh-quotient check + tuned eigensolver); mid-side nodes are included automatically when a face is constrained/loaded; sparse-LU ordering is chosen by matrix density.
- **Interoperability (kept as a feature, never runs `ccx`):** CalculiX/Abaqus `.inp` **export** of the full model/step, `.inp` and Gmsh 2.2 `.msh` **import**.

### Data model & API
- `projects` gained `element_type`, `settings`, `reference_points`, `constraints`, `node_sets` (idempotent migration, tested on a legacy PostgreSQL table).
- New/changed endpoints: `mesh/primitive` (6 shapes × 4 element types), `mesh/append` (extra body), `mesh/import`, `mesh/quality`, `settings`, `reference-points`, `constraints`, `node-sets`, `export/project`, `export/inp`, `projects/import`, `jobs/latest`, `capabilities`. BC/load schemas extended (typed BCs, 9 load kinds).
- Jobs run the engine, report progress, return readable errors (no tracebacks for user mistakes), and are limited to 2 concurrent per user with stale-job expiry.
- Plans: Free = static only; Pro = all eight analysis types (element order/type is not gated).

### PrePoMax features (task 1) — added in the UI
| Area | Added |
|---|---|
| Layout | PrePoMax-style workspace: model tree (left), setup/results (right), message window (bottom), status bar; navbar kept |
| Geometry/mesh | tube, plate-with-hole, L-bracket; Tet4/Tet10/Hex8/Hex20; add extra body; import `.inp`/`.msh`; mesh statistics & quality |
| Material | thermal expansion, plasticity, 9 presets, SI units note |
| Analysis | per-type parameter panels, amplitude curves, Rayleigh damping, initial/reference temperatures, centrifugal & gravity |
| Selection | all / invert / clear, by coordinate range, by face angle (flood fill), by node-id list, **named sets** |
| Supports & loads | typed menus for 14 kinds incl. symmetry, prescribed displacement, remote loads, convection, radiation |
| Constraints | reference points, rigid bodies, ties |
| Viewport | Z-up scene + grid, standard views, mesh edges, undeformed ghost, **section plane**, min/max markers, probe, support/load markers, reference-point markers, animation (modes + deformation), scale/auto-scale, fixed legend range |
| Results | all stress/strain/thermal fields, mode & buckling browser, time-frame slider with play, history chart, summary table, results restored after reload |
| Files | save/open project (`.gfea.json`), export `.inp`, results to **CSV**, **VTK**, **PNG** |
| Feedback | message window with solver log and readable errors |

## 2. Verification performed
| Check | Result |
|---|---|
| Closed-form benchmarks (`tests/fea`, 34 tests) | all pass; accuracy table in `SOLVER_REFERENCE.md §8` (e.g. frequency +0.15%, buckling +0.16%, Tet10 cantilever −0.3%) |
| API end-to-end (`tests/test_simulation_api.py`, 15 tests) | every analysis type via HTTP, failure messages, ownership, plan gating, file round-trips |
| Account/billing regression (32 tests) | unchanged, pass |
| Full suite on **SQLite** and **PostgreSQL 16.4** | 81 passed on both |
| Browser (headless Edge): create → mesh → select by coordinate → support → pressure → run → contour/section/edges/min-max; modal (animated, dark theme); buckling error; dynamic with frame slider; transient heat; constraints panel | works, no page errors |
| `vite build` | succeeds |

## 3. Known limitations (honest list)
1. **Parity is partial by design** — see the roadmap matrix: no friction. *(Contact between parts, undo/redo and the iterative solver were added afterwards - see `UNDO_ITERATIVE_CONTACT.md`; large deformation, beams/trusses/shells/springs and CAD import after that - see `NLGEOM_STRUCTURAL_CAD.md`.)* *(Multiple parts and per-part materials were added afterwards — see `PARTS_AND_MATERIALS.md`.)*
2. Hex8 shows shear locking and Tet4 is stiff in bending (physics of those elements); the UI recommends the quadratic types.
3. The section plane clips the surface; it does **not** draw a filled cap.
4. The mesh-edge overlay shows the triangulation of quad faces on brick meshes (diagonals visible).
5. Dynamics assume zero prescribed displacement; stresses are computed at the peak-displacement frame only; transient heat does not support radiation.
6. Tie needs the master surface saved as a **face set**; it interpolates linearly from corner nodes.
7. Result JSON: stress/strain *components* are dropped above 30,000 nodes; frames and large mode sets are stored for surface nodes only.
8. Direct solver: ≈ 30–40k DOFs take ~6–9 s; larger models switch automatically to the iterative solver (see `UNDO_ITERATIVE_CONTACT.md`).
9. Frontend is covered by manual/browser checks only — there are no automated UI tests.
10. The `/app` route currently renders without the sign-in guard (you edited `App.jsx`); the API still requires login, so a signed-out visitor sees an empty workspace and 401s.
11. Docker images were still not built here (daemon unavailable), nor was PayMongo exercised (unchanged from the previous change set).
