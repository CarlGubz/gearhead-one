# Undo/Redo · Iterative Solver · Contact

Plan first (§1–§4); results are appended in §5 when each part is verified.

## 1. Undo / redo
**Goal:** every model edit can be undone and redone (Ctrl+Z / Ctrl+Y and toolbar buttons), including mesh changes, part
edits, materials, supports, loads, constraints, sets, analysis settings.

**Design:** server-side history so it survives reloads and works with the existing "reassign the whole JSON column" edit style.
- Table `project_history(id, project_id, seq, label, data)`; `data` = zlib-compressed JSON snapshot of the model fields
  (mesh, parts, materials, material, BCs, loads, constraints, reference points, sets, settings, analysis type, gravity).
- `projects.history_pos` + `projects.history_labels` (list of labels, one per state) so every project response carries
  `{can_undo, can_redo, undo_label, redo_label}` without extra queries.
- `_commit()` is the single choke point for edits: it reads SQLAlchemy attribute history to reconstruct the *before* state
  (stored lazily as state 0 on the first edit), commits, then stores the *after* state, truncating any redo branch.
- Keep the last 20 states per project (oldest dropped). `POST /undo`, `POST /redo`, `GET /history`.
- Results are cleared on undo/redo like on any edit.

**Verification:** undo/redo round trips for each edit kind; redo branch is discarded by a new edit; cap of 20; other users blocked;
legacy projects (no history) start clean; restoring a mesh-changing step restores nodes/elements/faces/parts exactly.

## 2. Iterative linear solver
**Goal:** solve models beyond what sparse LU can handle (memory/time), without losing the direct solver's robustness.

**Design (`fea/linsolve.py`):**
- Common interface `factor(A) -> object.solve(b)`.
- `DirectSolver` (existing sparse LU with the density-based ordering).
- `PCGSolver` for symmetric positive-definite systems: conjugate gradients with an incomplete-LU (`spilu`) preconditioner,
  relative-residual tolerance (default 1e-8), iteration cap; reports iterations/residual; on non-convergence falls back to
  direct (below a size limit) or raises a clear error.
- `settings.linear_solver` = `auto` (default: iterative above ~80,000 free DOFs, direct below) | `direct` | `iterative`.
- Used by static, nonlinear Newton steps, dynamics, steady and transient heat. Frequency/buckling keep direct shift-invert factorisation
  (documented), because ARPACK needs repeated exact solves.
- No new dependency (pyamg has no wheel for this Python); SciPy only.

**Verification:** PCG vs direct agree (1e-6) on bar/cantilever/thermal/transient/nonlinear problems for all element types; iteration counts logged;
a model that is large for LU is solved iteratively and compared with a known closed form; singular systems are still reported.

## 3. Contact (frictionless, node-to-surface penalty)
**Goal:** parts can touch and push on each other without penetrating; separate when pulled apart.

**Formulation:**
- Contact pair `{type: "contact", master_part, slave_part, penalty?}` stored with constraints.
- Only slave faces that look at the master surface (nearby, opposing normals) form the contact surface; lateral faces of the slave
  part are excluded, otherwise edge nodes would collect tributary area they do not own.
- Each Newton iteration: slave surface nodes are projected on the master surface in the **current** configuration
  (closest point on the master face with its own shape functions, tri3/tri6/quad4/quad8); gap `g = n·(x_s − x_c)`
  with the outward master normal; a node is active when `g ≤ 0`. Only master faces whose normal opposes the slave body's outward
  normal at that node are candidates - this stops a node that sank below the master's edge from being "released" onto the
  neighbouring side face.
- Normal penalty force `F = ε A_s (−g)` on the slave node (area `A_s` = HRZ-lumped tributary area), equal and opposite on the master face nodes
  by the shape-function weights. Tangent = `ε A_s bbᵀ` (small-sliding, normal stiffness only). Frictionless.
- Penalty `ε = factor · E_ref / h` (E_ref = stiffer material, h = mean slave face size), `factor` default 10 (user adjustable).
- Driven by the existing incremental Newton loop (load stepping + cut-back); a static analysis with contact runs through it automatically.
  Converged only when the active set stops changing.
- A tiny diagonal stabilisation (1e-9·mean(diag K)) in the Newton matrix keeps frictionless tangential rigid-body freedom from making the
  solve singular (it does not enter the residual).
- Results: `contact_pressure` (Pa, at slave nodes), `contact_gap` (m), total contact force.
- Supported in `static` and `nonlinear`; other analysis types reject contact with a clear message.

**UI:** Constraints panel → "Contact between parts" (master, slave, penalty factor); contact pressure contour; messages report the number of active nodes.

**Verification (closed form):**
| Test | Expected |
|---|---|
| block on a fixed base, pressure on top | contact force = p·A; uniform pressure ≈ p; block shortening = ph/E (+ penalty penetration) |
| initial gap closed by prescribed displacement | force = A·E·(δ − g₀)/h |
| pulled apart / not touching | zero contact force |
| non-matching meshes, Tet10/Hex20 | force balance to 1e-6, pressure within a few % |
| equilibrium | Σ slave contact force = −Σ master contact force |
| penalty sensitivity | penetration ∝ 1/ε, global answer converges |

## 4. Out of scope (next)
Friction, large sliding with finite-deformation kinematics, self-contact, contact in dynamics/thermal (thermal contact conductance),
mortar/segment-to-segment integration (current node-to-surface is first-order accurate), augmented-Lagrangian refinement.

## 5. Results (what was actually built and measured)

### 5.1 Undo / redo
Server-side: every editing endpoint goes through one `_commit(db, project, label)`; before/after states are compressed JSON snapshots in
`project_history` (cap 20, redo branch truncated on a new edit). `GET/POST /api/projects/{id}/history|undo|redo`.
UI: Undo / Redo buttons with the action label as tooltip, `Ctrl+Z` / `Ctrl+Y` (ignored while typing in a field).
Tested by `tests/test_history.py` (7 tests): undo/redo of mesh, loads, constraints, parts and materials, redo truncation, cap, ownership.
Limit: each snapshot contains the full mesh, so a very large mesh x 20 states is heavy in the database; history is per project, not per browser tab.

### 5.2 Iterative linear solver
Two-level additive-Schwarz preconditioned CG (recursive-coordinate-bisection subdomains with overlap, rigid-body-mode coarse space).
Setting `linear_solver` = `auto` (default: iterative above `auto_threshold`, default 80,000 free DOFs) | `direct` | `iterative`; `solver_tolerance` (default 1e-8).
A failed iterative solve falls back to the direct solver. Used by static, nonlinear, dynamic and heat analyses; frequency and buckling keep the direct
shift-invert factorisation.
Measured: 173k equations solved in about 40 s with 86 PCG iterations (the direct solver is memory-bound there); on moderate problems the iterative
result matches the direct one to 1e-6 relative. Tests: `tests/fea/test_linear_solver.py` (13) + `tests/test_solver_api.py` (1).
Rejected on the way: ILU-preconditioned CG (the preconditioner is not symmetric, CG stalled) and AMG (`pyamg` has no wheel for this Python).

### 5.3 Frictionless contact
Implemented as planned in section 3 (node-to-surface penalty, current-configuration projection, HRZ tributary areas, tangent `eps*A*b*b^T`,
active-set check inside the incremental Newton loop) with the face-selection rules written there.

Verified (`tests/fea/test_contact.py`, 15 tests; `tests/test_contact_api.py`, 3 tests) - a block pressed onto a base, all four element types,
matching and non-matching meshes:
* contact force = p*A to within 1 %, base reaction = p*A to 1e-5;
* block shortening vs the closed-form series stiffness `p*(h1+h2)/E + p/eps`: **Hex8 on matching meshes agrees to 4 digits** (6.1e-6 m both);
  Tet4/Tet10 within 1 %, non-matching meshes within 3 %, Hex20 within 3 % (see limits);
* an initial gap closed by a prescribed displacement gives the force `A*(d-g0)/(h/E + 1/eps)`; bodies that stay apart carry exactly zero force and just translate;
* contact forces on slave and master are self-equilibrated and the tangent is symmetric positive semi-definite;
* penetration scales as 1/penalty while the global answer changes by under 2 %;
* contact with modal / buckling / thermal / dynamic analyses is rejected with a readable message; contact between a part and itself is rejected (API: 422).

Two real bugs were found by these tests and fixed: the tributary area of slave edge nodes included the lateral faces (the areas summed to twice the
contact area), and a node that sank below the master's edge was released onto the neighbouring side face (a silently wrong equilibrium: 8.0 MN
instead of 19.7 MN in the gap-closing test).

### 5.4 Known limits (not hidden)
* **Penalty node-to-surface**: the resultant is exact, the *nodal* pressure is not smooth (it can vary 2-3x between nodes under a uniform load, more on
  20-node bricks, whose consistent nodal loads under uniform pressure are partly negative and cannot be carried by a unilateral contact). Read the contact
  force and the displacements, not single nodal pressures.
* Frictionless, small-sliding tangent (normal stiffness only). Large sliding or large rotations are not validated; the slave-normal test uses the reference normal.
* Hertz-type curved contact, friction and self-contact were **not** verified or built.
* Every body must be held in place by supports (frictionless bodies can slide); a tiny diagonal regularisation stops the solve from failing but an unsupported body drifts.
* Contact is available in static and nonlinear analyses only. A pressure-loaded body that starts with a gap and has no other support is an ill-posed static problem and does not converge.
* The penalty factor (default 10) trades penetration against convergence speed.
