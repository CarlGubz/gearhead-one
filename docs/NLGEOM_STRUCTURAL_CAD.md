# Large deformation, structural elements and CAD import

Plan, design decisions, verification targets and (at the end) results for the three remaining "not built" items:

1. **Large-deformation analysis (NLGEOM)** for the solid elements.
2. **Truss, beam, shell and spring/dashpot elements.**
3. **CAD import (STEP / IGES / STL).**

Each item is built, tested against closed-form or independent references, and documented here. Limits are written down, not hidden.

---

## 1. Large deformation (NLGEOM)

**Goal:** solids that rotate and deflect a lot (cantilever tip at 30 % of its length, a rubber block stretched 50 %) give the right answer, which the small-strain solver cannot.

**Formulation (total Lagrangian, vectorised like the existing assembly):**
- Kinematics at every Gauss point: `F = I + Σ u_a ⊗ ∇₀N_a`, Green–Lagrange strain `E = ½(FᵀF − I)`.
- Internal force `f = ∫ B_L(F)ᵀ S dV₀`, tangent `K = ∫ B_Lᵀ C B_L dV₀ + K_σ(S)` (material + geometric/initial-stress part).
- Material models (selectable `finite_strain_model`):
  - `svk` – Saint Venant–Kirchhoff: `S = λ tr(E) I + 2μ E`. Large rotations, small-to-moderate strains. Default.
  - `neohookean` – compressible Neo-Hookean: `S = μ(I − C⁻¹) + λ ln J C⁻¹` with the matching algorithmic tangent. Large strains (rubber-like).
- Results: Cauchy stress `σ = F S Fᵀ / J` at the Gauss points -> the existing recovery (von Mises, principal …); strain output is Green–Lagrange.
- Runs through the existing incremental Newton driver (load stepping, cut-back, contact, solver choice), switched by the setting `nlgeom: true`. Displacements, reactions and contact stay in the same format; the viewport shows the deformed shape as before.
- Out of scope (rejected with a clear message): NLGEOM together with plasticity, thermal strain, modal/buckling/dynamic/thermal analyses. Loads are *dead* (they do not rotate with the structure) unless noted in the results section.

**Verification:**
| Test | Expected |
|---|---|
| rigid rotation `u = (R−I)X` of a whole mesh, any angle | internal force and stress exactly zero (the test that small-strain codes fail) |
| small load | NLGEOM = linear solution (1e-6) |
| uniaxial stretch, SVK and Neo-Hookean (single element, prescribed stretch 1.0–2.0) | closed-form Cauchy stress |
| cantilever with tip load, `PL²/EI` = 0.25 … 2 | elastica tip deflections (Mattiasson): `PL²/EI = 1` -> 0.3017 L vertical, 0.0560 L horizontal |
| consistent tangent | finite-difference check of K (quadratic convergence: ≤ 6 Newton iterations per increment) |
| contact + NLGEOM | runs; force balance holds |

---

## 2. Truss, beam, shell and spring/dashpot elements

**Design decision.** The solid engine has 3 DOFs per node and a long list of features built on that (ties, contact, thermal, plasticity). Structural elements need rotations (6 DOFs per node). Rather than destabilise the solid pipeline, **structural models are a separate model class** with their own assembly, boundary conditions, solver, and recovery in `app/fea/structural.py`:

| Element | Nodes | DOFs used | Section/property (per part) | Notes |
|---|---|---|---|---|
| `T3D2` truss | 2 | translations | area `A` | axial force only |
| `B31` beam | 2 | 6 | `rect`, `circle`, `pipe` or general `A, Iy, Iz, J` + orientation vector | 3-D **Timoshenko** beam (shear deformation); sectional forces N, Vy, Vz, T, My, Mz |
| `S4` shell | 4 | 6 | thickness `t` | flat-facet **MITC4** (assumed transverse shear, no shear locking) + Wilson/Taylor incompatible-mode membrane; drilling stabilisation; membrane forces, moments, top/bottom stress |
| `SPRING2` | 2 | translations | stiffness `k`, damping `c` | axial spring and/or dashpot between two nodes (a spring to ground = fix the other node) |

A structural project has **one element type** (stored in `element_type`), parts keep their own material and now also their own section. Mixed beam + shell models are not supported (documented limit).

- Boundary conditions: `dofs` 0–5 (ux uy uz rx ry rz) – fixed, pinned (0-2), symmetry, prescribed.
- Loads: nodal force and **moment**, pressure on shells, gravity, uniform line load on a beam part, point mass.
- Analyses: static, modal, linear buckling (truss and beam), transient dynamic (Newmark, Rayleigh + dashpot damping).
- Results: displacements and rotations, reactions (force and moment), nodal fields (von Mises / bending stress etc.), beam sectional forces per element.
- Not supported on structural models (rejected with a clear message): ties, rigid bodies, contact, thermal analyses, plasticity, NLGEOM, `.inp` export. Solid + structural coupling is not available.

**Verification:**
| Test | Expected |
|---|---|
| truss bar, 2-bar truss, 3-bar planar truss | `δ = PL/EA`; member forces by statics |
| spring chain, spring-mass frequency, dashpot decay | `δ = F/k`, `f = ½π√(k/m)`, log decrement |
| cantilever beam tip load / uniform load / end moment | `PL³/3EI` (+ shear term), `qL⁴/8EI`, `ML²/2EI` |
| simply supported beam, point load | `PL³/48EI` |
| portal frame sway | closed form |
| beam modal | `f = (βL)²/(2πL²)√(EI/ρA)` (cantilever 1.875², 4.694², …) |
| Euler column buckling | `π²EI/(KL)²` for pinned and cantilever |
| torsion of a bar | `TL/GJ` |
| shell cantilever plate under tip load, plate under pressure | Euler–Bernoulli / Navier series for clamped and simply supported plates |
| patch tests | constant-stress membrane and constant-curvature bending reproduced exactly |
| Scordelis–Lo roof | published displacement 0.3024 (converges from mesh refinement) |
| pinched/pressurised cylinder, thin tube | membrane `σ = pR/t` |
| rigid-body modes | exactly six zero eigenvalues of the free shell/beam |

---

## 3. CAD import (STEP / IGES / STL)

**Design decision.** Reading B-rep CAD and meshing a volume needs a geometry kernel; writing one is not realistic. We use **Gmsh** (Python API, bundles OpenCASCADE) as an optional dependency:
- `POST /api/projects/{id}/mesh/cad` (multipart: file, `size` = target element size, `order` = 1 or 2; later also `family` tet / hex) → Tet4 / Tet10 (or Hex8 / Hex20) volume mesh via Gmsh. One part per CAD solid (so assemblies import as several parts, with their own materials).
- STEP (`.step`, `.stp`), IGES (`.iges`, `.igs`) via OpenCASCADE; STL (`.stl`, ASCII or binary) by classifying the surface mesh and meshing the enclosed volume (the STL must be watertight).
- Safety: size limit, time limit, runs in a worker thread, node limit as for other meshes, readable errors for an open/non-manifold surface, empty file or unsupported units.
- Gmsh not installed → endpoint answers 501 with an install hint and the UI hides nothing (it explains).
- Licensing note (Gmsh is GPL-2+ with an OpenCASCADE exception): an internal service is fine; shipping the Docker image to third parties needs a licence review.

**Verification:** STEP/IGES/STL boxes, cylinders and an assembly of two solids generated with Gmsh itself and re-imported: volume within 1 % (STL facets) / 1e-6 (STEP boxes), expected part count, mesh passes the quality check and solves a static problem against the closed form; open STL, text file renamed `.step` and oversized size parameter are rejected cleanly.

---

## 4. Order of work
1. NLGEOM (engine, API, UI, tests).
2. Structural core (elements, assembly, analyses, recovery) with its tests.
3. Structural API and UI (mesh generators, sections, viewport lines/shells, results).
4. CAD import (endpoint, UI).
5. Full test suites on SQLite and PostgreSQL, build, browser check, docs.


---

## 5. Results (what was built and measured)

All numbers below come from the automated tests (`tests/fea/test_nlgeom.py`, `tests/fea/test_structural.py`, `tests/test_nlgeom_api.py`,
`tests/test_structural_api.py`, `tests/test_cad_import.py`; 73 new tests, full suite 223 on SQLite and on PostgreSQL) and from a browser run
of the new screens.

### 5.1 Large deformation (NLGEOM)
Total Lagrangian solid mechanics in `app/fea/finite.py`, switched on with the setting `nlgeom` (Analysis panel: *Large deformation*) in static and
nonlinear analyses; material models `svk` (default) and `neohookean`. It reuses the incremental Newton driver, so contact, the linear-solver choice
and increment cut-back work as before; an inverted trial element cuts the increment back instead of failing.

| Check | Result |
|---|---|
| rigid rotation (110 degrees plus translation) of Tet4/Tet10/Hex8/Hex20 meshes | internal force and stress zero to round-off (the small-strain assembly is wrong by > 1000x here) |
| consistent tangent vs finite differences (SVK and Neo-Hookean) | agrees to 1e-6, symmetric |
| homogeneous stretch, Cauchy stress | exact for both models |
| uniaxial stretch 1.5 - 2.5 solved by Newton (nu = 0, closed form P = mu (L - 1/L) for Neo-Hookean, mu L (L^2 - 1) for SVK) | reaction and Cauchy stress within 2e-4 |
| cantilever tip load, PL^2/EI = 1, 2, 3 (Hex20, 20 elements, Mattiasson elastica) | tip drop 0.3022 / 0.4944 / 0.6045 L vs 0.3017 / 0.4939 / 0.6033 (+0.15 %, +0.10 %, +0.21 %); shortening +0.72 %, +0.39 %, +0.01 % |
| small loads | reproduces the linear solution (1e-4) for SVK and Neo-Hookean |

Known limits: elastic only (a yield stress or a temperature change is rejected with a clear message), loads are **dead** (pressure and traction do
not follow the rotating surface), no NLGEOM in modal / buckling / dynamic / thermal analyses, Saint Venant-Kirchhoff loses accuracy beyond roughly
50 % compressive strain (use Neo-Hookean for rubber-like response).

### 5.2 Structural elements (`structural.py`, `struct_runner.py`, `structmesh.py`)
Implemented as a separate model class with 6 DOFs per node. A project holds **one** element type; parts carry material and section.

| Element | Formulation |
|---|---|
| `T3D2` truss | axial stiffness EA/L, lumped mass, geometric stiffness for buckling |
| `B31` beam | 3-D Timoshenko beam (shear deformation through the shear areas), rect / circle / pipe / general sections, orientation vector, consistent mass and geometric stiffness, uniform line loads, self-weight, sectional forces N, Vy, Vz, T, My, Mz |
| `S4` shell | flat-facet MITC4 (transverse shear tied at the mid-sides, no shear locking), membrane with incompatible modes (Taylor modification) condensed out, small drilling stabilisation, consistent mass, pressure / traction / gravity; stress resultants and top/bottom von Mises |
| `SPRING2` | spring k and dashpot c along the element axis (a spring to ground = fix the other node) |

Analyses: static, modal, linear buckling (truss and beam), transient dynamic (Newmark, Rayleigh + dashpot damping). The solver detects an under-supported
structure (a zero-energy mode of the stiffness matrix) and says so instead of returning an arbitrary rigid-body motion.

| Check | Result |
|---|---|
| bar, two-bar truss, spring chain | exact (PL/EA, P L / (2 E A sin^2 a), F (1/k1 + 1/k2)) |
| spring-mass frequency, dashpot step response overshoot | exact; overshoot within 0.2 % of 1 + exp(-pi z / sqrt(1 - z^2)) |
| cantilever tip load (bending + shear), uniform load, self-weight, end moments (both bending planes), torsion | exact to the 7 printed digits |
| simply supported beam, L-frame by the unit-load method (vertical member, orientation handled) | exact |
| beam frequencies (12 elements, modes 1-3) | error 0.000 %, 0.002 %, 0.012 % |
| Euler buckling, pinned-pinned and clamped-free (10 elements) | +0.0014 % and +0.0001 % |
| membrane and bending patch tests on a distorted mesh | pass: exact fields and uniform stress resultants |
| square plate under pressure vs plate theory (Navier series / clamped coefficient), centre deflection as a fraction of theory | simply supported 0.996 (8 x 8), 1.000 (16 x 16); clamped 0.995 (8 x 8), 1.003 (16 x 16) |
| simply supported plate first frequency (8 x 8) | +1.7 % |
| free plate | six rigid-body modes at 0 Hz |
| cantilever strip vs beam theory (20 x 2) | within 1 % |
| thin cylinder inflated (flat facets) | hoop stress and radial growth equal the faceted-ring result cos(dphi/2) p R / t to 0.2 % |
| Scordelis-Lo roof (quarter model) | 0.3163 (4 x 4), 0.3052 (8 x 8), 0.3068 (16 x 16), 0.3090 (24 x 24); published values for this benchmark range from about 0.300 to 0.309 depending on the reference solution |

API / UI: `POST /mesh/structural` (line, portal, plate, cylinder), `POST /mesh/structural/append` (new part, coincident nodes are joined),
`POST /mesh/structural/custom` (coordinates and connectivity), part `section` editing, 6-DOF supports, moment, line-load and point-mass loads.
Beams, bars and springs are drawn as thin tubes, shells as their quads; contours work on both.

Known limits (not hidden):
* one element type per project; no mixed beam + shell models and no coupling of structural and solid parts;
* no ties, contact, rigid bodies, reference points, thermal, plasticity, NLGEOM or `.inp` export on structural models (each is rejected with a message);
* shells are **flat facets**: curved shells converge with refinement but are not exact at coarse meshes; warped quads are projected on their mean plane;
  no shell-to-beam rotation compatibility for folded junctions beyond the small drilling stiffness;
* beams: constant section per part, no tapered or offset sections, no warping torsion, no end releases (hinges), torsion stress is approximate;
* buckling covers truss and beam members only; the shell stress-resultant axes are the projection of the global X axis on each facet;
* pressure sign: positive pushes against the element normal (right-hand rule of the node order).

### 5.3 CAD import
`POST /api/projects/{id}/mesh/cad` (multipart: file, `size` in metres or 0 for automatic, `order` 1 or 2, `unit` mm / cm / m / in; since the Gmsh
extension also `family` = `tet` / `hex` and `options` = JSON with any Gmsh option, and `.brep` files). Gmsh (OpenCASCADE) runs in a
**separate process** (`gmsh_worker.py`, formerly `cad_worker.py`) with a 180 s time limit, so a kernel crash or hang cannot take down the API; one part per solid.
Bricks, algorithm choices, refinement regions and the other options: [`MESHING_GMSH_AND_SELECTION.md`](MESHING_GMSH_AND_SELECTION.md).

| Check | Result |
|---|---|
| 100 x 20 x 10 mm box as STEP, IGES (surfaces sewn into a solid) and STL, Tet10 | volume 2.000000e-5 m^3, exact to 1e-6 |
| unit handling (mm, cm) and Tet4 | correct scaling |
| STEP cylinder | volume within 1 % (curved boundary) |
| STL cylinder at 3 mm | within 2 % of the exact volume; the volume mesh converges to the STL's own volume (99.7 %) as the size drops |
| two-solid STEP assembly | two parts, correct total volume |
| imported bar solved in tension | tip displacement within 0.2 % of sigma L / E |
| junk file, empty file, open (non-watertight) STL, wrong extension, wrong unit / order, absurdly small size | clean 400 messages, nothing is installed |
| import is part of undo / redo; other users get 404 | yes |

Safeguards found necessary while testing: flat sliver tetrahedra that Gmsh creates on STL surfaces are dropped; second-order mid-side nodes stay on straight
edges for STL (and fall back to straight edges for STEP/IGES if a curved element would fold).

Known limits: STL must be a closed manifold surface and the volume mesh re-parameterises it (the STL facet size is not kept); no feature suppression or geometry healing beyond what
OpenCASCADE does on read; multi-solid assemblies are meshed per solid (touching faces are not conforming - join them with a *Tie between parts*); bricks of a CAD file are made by splitting tetrahedra (Hex8 / Hex20, see the Gmsh document);
STEP colours and names are ignored; units are chosen by the user, not read from the file.
**Gmsh is GPL-2+ (with an OpenCASCADE linking exception).** Running it inside your own service is fine; redistributing a Docker image that contains it to third parties
needs a licence review. The Docker image installs the shared libraries Gmsh needs (`libglu1-mesa` and friends) - **that image build was not run in this environment.**
