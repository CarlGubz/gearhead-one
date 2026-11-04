# Standalone Solver — Technical Reference

The engine lives in `backend/app/fea/` and uses only NumPy/SciPy. **No external solver (CalculiX or otherwise)
is installed, called or required.** `.inp` export/import exists purely for interoperability.

## 1. Conventions
- **Units:** SI everywhere — m, kg, s, N, Pa, W, °C (temperatures are treated as °C; radiation converts with `absolute_zero = -273.15`).
- **DOFs:** node *n* has displacement DOFs `3n..3n+2`; thermal problems use one DOF per node. Reference points add 6 DOFs each (ux, uy, uz, rx, ry, rz).
- **Voigt order:** `[xx, yy, zz, xy, yz, zx]`, engineering shear strains.
- **Pressure:** positive = pushing **into** the surface (compressive), as in CalculiX/PrePoMax. Surface heat flux: positive = **into** the body.
- **Z-up** coordinate system in the viewport (gravity default −Z).

## 2. Element library (`elements.py`)
Node ordering follows CalculiX/Abaqus so `.inp` files round-trip.

| Type | Nodes | Integration (stiffness) | Mass | Notes |
|---|---|---|---|---|
| `C3D4` | 4 | 1 point (exact) | consistent | constant strain — stiff in bending |
| `C3D10` | 10 | 4 points | HRZ lumped (corner V/36, mid 4V/27) | recommended tet |
| `C3D8` | 8 | 2×2×2 | consistent | shear-locks in bending |
| `C3D20` | 20 | 3×3×3 | consistent | recommended brick |

Faces for loads: `tri3`, `tri6` (Dunavant degree-4 rule), `quad4` (2×2), `quad8` (3×3). Shape functions, derivatives and
all rules are unit-tested (partition of unity, Kronecker property at nodes, finite-difference derivatives, quadrature exactness).

## 3. Materials (`materials.py`)
Isotropic linear elasticity (`E`, `ν`), density, conductivity, specific heat, thermal expansion `α`. **Per-element materials:** a `MaterialMap` assigns a material to each element (via parts); stiffness, thermal strain, plasticity, mass, conductivity, capacity and body loads are all evaluated per material.
**Plasticity:** J2 (von Mises) with linear isotropic hardening (`yield_stress`, `hardening = dσ_y/dε_p`), radial-return
update with the **consistent algorithmic tangent** so the global Newton converges quadratically. Small-strain formulation.

## 4. Analyses (`analyses.py`, dispatched by `runner.py`)
| Type | Formulation | Output |
|---|---|---|
| `static` | `K u = f`, optional uniform ΔT (thermal pre-strain), gravity, centrifugal, pressure, traction, nodal & remote loads | u, reactions, σ, ε, von Mises, Tresca, principal, strain energy |
| `nonlinear` | incremental load steps, Newton–Raphson with J2 plasticity, automatic increment cut-back (down to 1e-4) | + equivalent plastic strain |
| `static` / `nonlinear` + `nlgeom` | total Lagrangian large deformation (Saint Venant–Kirchhoff or Neo-Hookean), elastic; see `NLGEOM_STRUCTURAL_CAD.md` | Cauchy stress, Green–Lagrange strain |
| `modal` | `K φ = ω² M φ`, shift-invert Lanczos (ARPACK); with no supports → free-free with 6 rigid-body modes | frequencies (Hz), mode shapes |
| `buckling` | linear eigenvalue: `(K + λ K_σ)φ = 0` using the static stress for the reference load | load factors λ, shapes; refuses tension-only loads with a clear message |
| `dynamic` | implicit Newmark-β (β=¼, γ=½), Rayleigh damping `C=αM+βK`, amplitude curves (step/ramp/sine/pulse), one LU factorisation | ≤50 displacement frames, history at chosen nodes, stresses at the peak frame |
| `thermal` | steady conduction + convection film + **radiation** (Newton on T⁴) + fixed T + nodal/surface/volumetric heat | T, heat flux |
| `thermal_transient` | `C Ṫ + K T = Q`, θ-method (1 = backward Euler, 0.5 = Crank–Nicolson), amplitude curve | ≤50 frames, history |
| `thermomech` | sequential: heat (steady or transient final state) → static with thermal strain `α(T−T_ref)` | all of the above + temperature |

Linear systems are partitioned (prescribed DOFs are eliminated exactly) and solved by sparse LU; the fill-reducing ordering is
chosen from the matrix density (`MMD_AT_PLUS_A` for tets/linear bricks, `COLAMD` for 20-node bricks — measured 2–4× faster each way). Singular systems are detected by a relative-residual check and reported as
"model is not fully constrained" instead of returning garbage.

## 5. Constraints (`constraints.py`)
Linear master/slave elimination `u = T·u_red`.
- **Rigid body:** selected surface follows a reference point (`u = u_ref + θ × r`). Loads/supports/prescribed motion go on the reference point.
- **Tie:** slave nodes follow the closest master face through that face's own shape functions (tri3/tri6/quad4/quad8), tolerance default `1e-3 × bbox diagonal`. Exact for matching meshes, first-order for non-matching; applies to displacements **and** temperatures. `master_part`/`slave_part` ties detect the interface automatically.
- Constraint chains are resolved; a node constrained twice, or a support on a slaved node, is rejected with an explanation.

## 6. Results recovery (`recovery.py`)
Stress/strain are evaluated at Gauss points, extrapolated to nodes per element with a least-squares linear fit in natural
coordinates, and averaged over the elements sharing each node. Fields: displacement (mag/x/y/z), von Mises, Tresca, principal S1/S2/S3,
six stress and six strain components, equivalent strain, equivalent plastic strain, strain energy density, temperature, heat flux (x/y/z/magnitude).

## 7. Mesh generation (`meshgen.py`)
Every primitive (box, cylinder, sphere, tube, plate-with-hole, L-bracket, cone, torus) can be meshed with every solid element type.
Box and L-bracket → Cartesian bricks (C3D8/C3D20) or Freudenthal-split tetrahedra (conforming); cylinder, cone, sphere, tube,
plate-with-hole and torus → **mapped structured bricks** (square→disc and cube→ball mappings, polar rings, an O-grid around the hole,
a swept section for the torus); the tetrahedra of cone and torus are those bricks split six ways, while cylinder, sphere, tube and
plate-with-hole keep their Delaunay tetrahedra (centroid filtering for holes). Quadratic meshes insert mid-side nodes and project
boundary mid-nodes of curved surfaces back onto the true surface (backing off where a full move would fold an element).
Every mesh is checked for inverted elements (tets and bricks that are inside out are auto-reoriented; a brick whose Jacobian
changes sign inside is rejected). The same shapes can be meshed by Gmsh, and CAD / `.msh` files can be meshed or read as tets or
bricks — see [`MESHING_GMSH_AND_SELECTION.md`](MESHING_GMSH_AND_SELECTION.md).

## 8. Verification (all automated — `backend/tests/fea`, `backend/tests/test_simulation_api.py`)
| Benchmark | Closed form | Observed |
|---|---|---|
| Uniaxial bar, all 4 element types | σ = F/A, δ = FL/EA | exact to 1e-8 |
| Cantilever tip deflection, 1 m × 0.1 m², 320–960 elems | PL³/3EI | Tet10 **0.9967**, Hex20 **0.9973**, Hex8 0.876 (locking), Tet4 0.51 (stiff) |
| Cantilever self-weight | wL⁴/8EI | within 6% |
| Free & restrained thermal expansion | αΔT·L, σ = −EαΔT | exact to 1e-6 |
| Cantilever first frequency (and the degenerate pair) | 1.875²/2πL² √(EI/ρA) | **+0.15%** (Tet10, Hex20) |
| Free-free modal | 6 rigid-body modes ≈ 0 Hz | verified |
| Euler column buckling | π²EI/4L² | **+0.16%** (Tet10), +0.14% (Hex20), +0.09% (10k-DOF sparse path) |
| Bilinear plasticity, uniaxial | ε = σ/E + (σ−σ_y)/H | exact to 1e-4; equals linear solution below yield |
| Plastic cantilever | converges, deflects more than elastic | verified |
| Rigid body / remote force with lever arm | exact slave motion; PL³/3EI and PL²/2EI | within 6% / exact |
| Tie between separately meshed bodies | same as monolithic mesh | identical to 1e-9 |
| 1-D conduction (all element types) | linear profile | exact to 1e-8 |
| Convection boundary, radiation equilibrium | analytic | 1e-8 / 1e-3 |
| Transient lumped cooling | T∞ + (T₀−T∞)e^(−t/τ) | within 0.2% |
| Newmark step response | overshoot factor 2 | within 8%; Rayleigh damping reduces amplitude |
| Uniform stress recovery | von Mises = Tresca = S1 = applied | exact |
| API end-to-end | each analysis through HTTP; error messages; ownership; file round-trips | 81 tests pass (SQLite and PostgreSQL) |

## 9. Limits & performance
Mesh ≤ 60,000 elements (generator) / 120,000 nodes (any mesh); ≤ 50 modes; ≤ 20,000 time steps; uploads ≤ 25 MB;
2 concurrent analyses per user; jobs older than 30 minutes are expired.
Measured static solves (laptop CPU, direct sparse LU): ≈ 19k DOFs (Tet4) 1.0 s; ≈ 31k DOFs (Tet10) ≈ 7 s; ≈ 37k DOFs (Hex20) ≈ 8 s.
Beyond ~150k DOFs a direct solver becomes memory-bound; the default `auto` setting therefore switches to a two-level Schwarz-preconditioned CG above
80,000 free DOFs (173k equations in about 40 s). See `UNDO_ITERATIVE_CONTACT.md`.

## 10. Not implemented (see the plan for the full matrix)
Friction and large-sliding contact (frictionless node-to-surface penalty contact between parts exists in static/nonlinear analyses); large deformation combined with plasticity, creep/viscoplastic materials; large-deformation pressure that follows the surface; mixed beam + shell models, tied or contacting structural parts
elements; orthotropic or temperature-dependent materials; multiple materials/parts per model; coupled thermo-mechanical solve
(sequential only); modal dynamics / steady-state dynamics / random response; CAD healing and hex meshing of CAD (STEP / IGES / STL import itself exists, see `NLGEOM_STRUCTURAL_CAD.md`).
