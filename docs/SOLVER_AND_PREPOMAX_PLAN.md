# Plan — Standalone Solver & PrePoMax Feature Parity

Companion log: [`SOLVER_AND_PREPOMAX_LOG.md`](SOLVER_AND_PREPOMAX_LOG.md) (written as work is completed and verified).

## 1. Honest scope statement
PrePoMax is a front end for CalculiX, a 25-year-old general-purpose FE code. Matching *everything*
either of them does (contact, NLGEOM, shells/beams, CAD import, CFD…) is a multi-year effort.
This plan therefore defines **what this change set delivers and verifies**, and lists the rest as an
explicit roadmap so nothing is implied that does not exist.

## 2. Solver: standalone engine (replaces CalculiX entirely)

### Decisions
| # | Decision |
|---|---|
| S0 | New package `backend/app/fea/` — pure NumPy/SciPy, **no external binary**. `calculix.py` and every `ccx` reference are removed (code, Dockerfile apt step, health flag, UI text). |
| S1 | Vectorised assembly (batched `einsum`) replaces the per-element Python loop — an order of magnitude faster. |
| S2 | Element library uses **CalculiX/Abaqus node ordering** so `.inp` import/export round-trips: `C3D4`, `C3D10`, `C3D8`, `C3D20`. |
| S3 | Every analysis is verified against a closed-form solution in an automated test (see §5). |
| S4 | A CalculiX-compatible `.inp` **exporter/importer** is kept as an *interoperability* feature (PrePoMax does the same). It never runs `ccx`. |

### Capability matrix (CalculiX → this solver)
| CalculiX capability | Status in this change |
|---|---|
| `*STATIC` linear elastic, C3D4/C3D10/C3D8/C3D20 | **Delivered** |
| `*MATERIAL` `*ELASTIC` `*DENSITY` `*EXPANSION` `*CONDUCTIVITY` `*SPECIFIC HEAT` | **Delivered** |
| `*PLASTIC` (J2, isotropic hardening) + incremental Newton | **Delivered** (small-strain) |
| `*FREQUENCY` | **Delivered** (consistent mass; HRZ for C3D10) |
| `*BUCKLE` (linear eigenvalue) | **Delivered** |
| `*HEAT TRANSFER` steady + transient (θ-method) | **Delivered** |
| `*UNCOUPLED TEMPERATURE-DISPLACEMENT` (sequential thermo-mechanical) | **Delivered** |
| `*DYNAMIC` implicit (Newmark-β) + Rayleigh damping, amplitude curves | **Delivered** |
| `*BOUNDARY` (fixed, prescribed, symmetry, pinned), `*CLOAD`, `*DLOAD` (P, gravity, centrifugal) | **Delivered** |
| `*DFLUX`, `*FILM`, `*RADIATE`, body heat | **Delivered** |
| `*RIGID BODY` / `*MPC` (reference point), `*TIE` | **Delivered** (rigid body via reference point; tie by node-to-face projection) |
| `*CONTACT PAIR` (frictionless, penalty, between parts) | **Delivered** (static/nonlinear; no friction) |
| Friction, large sliding, self-contact | Roadmap |
| `NLGEOM` (large deformation, SVK + Neo-Hookean, elastic) | **Delivered** (solids; no plasticity) |
| Creep, viscoplasticity | Roadmap |
| Truss (T3D2), beam (B31, Timoshenko), shell (S4, MITC4), spring/dashpot (SPRING2) | **Delivered** (separate 6-DOF model class) |
| S8 / B32 higher-order structural elements, shell-solid coupling | Roadmap |
| `*COUPLED TEMPERATURE-DISPLACEMENT`, cyclic symmetry, `*STEADY STATE DYNAMICS`, random response, `*MODAL DYNAMIC` | Roadmap |
| Orthotropic/anisotropic, temperature-dependent material data | Roadmap |
| Iterative solver for very large models | **Delivered** (Schwarz-preconditioned CG, automatic above 80k DOFs) |

## 3. PrePoMax feature parity

Legend: ✅ delivered here · 🟡 partly · 🗺️ roadmap (not built).

| PrePoMax area | Feature | Status |
|---|---|---|
| **Geometry** | Primitives box/cylinder/sphere | ✅ (existing) |
| | New primitives: tube, plate with hole, L-bracket | ✅ |
| | STEP/IGES/STL import (Gmsh / OpenCASCADE) | ✅ |
| | Import mesh: Abaqus/CalculiX `.inp` | ✅ |
| **Mesh** | Element order linear/quadratic, hex or tet | ✅ (hex for **every** primitive, CAD file and `.msh` — see `MESHING_GMSH_AND_SELECTION.md`) |
| | Mesh density control, mesh statistics & quality (volume, Jacobian, aspect) | ✅ |
| | Gmsh meshing of arbitrary CAD (Tet4/Tet10) | ✅ |
| | Mesh refinement fields, hex meshing of CAD | 🗺️ |
| **FE model** | Material: elastic, density, thermal expansion, conductivity, specific heat, plasticity | ✅ |
| | Material library (more presets) | ✅ |
| | Multiple parts and a material library (per-part materials), part move/hide/delete | ✅ (see `PARTS_AND_MATERIALS.md`) |
| | Reference points | ✅ |
| | Constraints: rigid body, tie (incl. automatic tie between parts, thermal ties) | ✅ |
| | Contact between parts (frictionless) | ✅ |
| | Friction, surface interaction properties | 🗺️ |
| | Amplitudes (step, ramp, sine) | ✅ |
| **Steps** | Static, nonlinear (plastic), frequency, buckling, steady heat, transient heat, thermo-mechanical, dynamic | ✅ |
| **BC** | Fixed/encastre, pinned, symmetry (X/Y/Z), prescribed displacement, fixed temperature | ✅ |
| **Loads** | Concentrated force, remote force/moment (reference point), pressure, gravity, centrifugal, thermal (ΔT), nodal heat, surface flux, convection film, radiation, body heat | ✅ |
| **Selection** | Click nodes/faces (existing), select all / clear / invert, by coordinate range, by face angle (flood fill), by node ID list | ✅ |
| | Named sets (save a selection and reuse) | ✅ |
| **Results** | Contours: displacement (mag/X/Y/Z), von Mises, Tresca, principal, stress components, strain, equivalent plastic strain, temperature, heat flux, strain energy density | ✅ |
| | Deformation scale, animation (play/loop), undeformed ghost, edges/wireframe toggle | ✅ |
| | Min/max markers, manual legend range, probe (click a node → values) | ✅ |
| | Section plane (clipping) | ✅ |
| | Modal/buckling mode browser; transient/dynamic time-frame slider | ✅ |
| | Export CSV, VTK legacy, PNG screenshot | ✅ |
| | History output plots (time curves at a node) | ✅ (transient/dynamic) |
| **Project** | Save/open project file (JSON), export `.inp`, units note (SI) | ✅ |
| | Undo/redo | ✅ |
| | Messages/log panel | ✅ |
| **App** | Standard views (iso/front/top/right/fit) | ✅ |

## 4. Architecture of the change
```
backend/app/fea/
  elements.py      shape functions, Gauss rules, faces (tri3/tri6/quad4/quad8)
  model.py         FEMesh (nodes, connectivity, boundary faces, quality)
  materials.py     elastic D matrix, J2 return mapping + consistent tangent
  assembly.py      K, M, thermal K/C, Kσ, body/face loads (vectorised)
  constraints.py   reference points, rigid body, tie -> transformation T
  analyses.py      static, nonlinear, frequency, buckling, heat, transient, thermomech, dynamic
  recovery.py      stress/strain/flux recovery, nodal extrapolation, derived scalars
  inp.py           CalculiX .inp export / import
  runner.py        project JSON -> analyses -> result JSON
backend/app/mesh_gen.py  (extends mesh.py)  primitives, hex/tet, quadratic, quality
backend/tests/fea/       analytic benchmarks
frontend/…               new panels, result tools, project I/O
```
Data model additions on `projects`: `element_type`, `settings` (step parameters), `reference_points`,
`constraints`, `node_sets`; new load/BC types stay inside the existing JSON columns.

## 5. Verification (each is an automated test)
| Benchmark | Closed form |
|---|---|
| Uniaxial bar, all 4 element types | σ = F/A, δ = FL/AE (exact) |
| Cantilever tip deflection (Tet10, Hex20) | δ = PL³/3EI (± few %) |
| Thermal expansion of a free bar | δ = αΔT·L, σ = 0; fixed bar σ = −EαΔT |
| Cantilever first frequency | f₁ = (1.875²/2πL²)√(EI/ρA) |
| Euler column buckling | P_cr = π²EI/(4L²) |
| 1-D steady conduction; transient slab | linear profile; series solution |
| Convection / radiation balance | analytic equilibrium temperature |
| Elastic–plastic bar (bilinear) | σ = σy + H·εp, unload to residual strain |
| Newmark SDOF-like bar | period vs. analytic |
| Rigid body & tie | rigid motion reproduced exactly; tie vs. continuous mesh |
| Rotational patch tests | constant-strain patch reproduced exactly |

## 6. Delivery order
1. This document → 2. `fea/` engine + tests → 3. mesh generators → 4. API/jobs/models, remove CalculiX →
5. frontend panels & result tools → 6. docs/README/Docker → 7. browser verification.
