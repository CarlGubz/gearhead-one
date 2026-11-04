# Multiple Parts & Materials

Status: **delivered** — plan in §1–§6, results in §7. Motivation: contact (and realistic assemblies) need
several bodies with different materials; this is the prerequisite listed in `SOLVER_AND_PREPOMAX_LOG.md`.

## 1. Requirements
1. A model holds **several parts** (bodies). Parts are created by meshing a primitive, "add as separate body", or `.inp` import.
2. A project has a **material library**; every part is assigned one material. Different parts may use different
   elastic, thermal, density and plastic properties.
3. Every analysis (static, nonlinear, frequency, buckling, dynamic, steady/transient heat, thermo-mechanical) honours
   per-element properties.
4. Parts can be **renamed, re-assigned, moved (translated), hidden, and deleted**.
5. Parts are joined with a **Tie between parts** (auto-detects which slave nodes lie on the master surface within a tolerance);
   manual node/face ties and rigid bodies keep working.
6. Files round-trip: project file (`.gfea.json`), `.inp` export (one `*MATERIAL` + `*SOLID SECTION` per part) and import
   (`*ELEMENT … ELSET=`, `*MATERIAL`, `*SOLID SECTION`).
7. Backwards compatible: existing projects open as a single part using their current material.

## 2. Data model
`projects` gains three JSON columns (idempotent migration):

| Column | Shape |
|---|---|
| `materials` | `[{id, name, E, nu, density, conductivity, specific_heat, alpha, yield_stress, hardening}]` — the **extra** library entries |
| `parts` | `[{id, name, elem_start, elem_end, material_id}]` — contiguous element ranges that tile the mesh; `material_id` is `"default"` (the existing `material` column) or a library id |
| `surface_parts` | part index of every render triangle (for colouring / hiding in the viewport) |

Legacy rows (NULL) are served as one part `p0 "Body"` using the default material.

## 3. Engine changes (`backend/app/fea`)
- `MaterialMap` (in `materials.py`): groups of elements sharing a `Material`, per-element property arrays
  (`prop("density")` …), `plastic` flag, chunk iteration per group. A single `Material` is still accepted everywhere.
- Mechanics (`mech_assemble`): `D`, thermal expansion and the J2 update are evaluated **per material group**; plastic state is
  global per element, so a model may be partly elastic, partly plastic.
- Mass, conductivity, heat capacity, body force (gravity/centrifugal use per-element density) and heat flux use per-element arrays.
- `ConstraintSystem` gains a *non-strict tie* (nodes outside the tolerance are skipped, but at least one node must tie).
- `runner.Model` builds the map from `parts` + `materials`, resolves `tie` constraints given as `master_part` / `slave_part`.

## 4. API
`POST/PUT/DELETE /api/projects/{id}/materials`, `PUT/DELETE /api/projects/{id}/parts/{pid}`,
`POST /api/projects/{id}/parts/{pid}/translate`, `mesh/append` accepts `name`/`material_id`; constraint schema gains
`master_part`, `slave_part`; project export/import carry the new fields. Deleting a part compresses the mesh and remaps (or drops)
every BC/load/constraint/set that referenced it.

## 5. UI
Left tree: **Materials** (library editor: choose, rename, new, delete) and **Parts** (name, material, move, hide, delete, colour swatch).
Viewport: parts tinted by part colour when no contour is shown; hidden parts disappear from drawing and picking.
Constraints panel: **Tie parts** (master part, slave part, tolerance).

## 6. Verification plan
| Test | Expected |
|---|---|
| two stacked bars, different E (ν = 0), tied, uniaxial load | δ = σ(L₁/E₁ + L₂/E₂) exactly, matching *and* non-matching meshes, all element types |
| series conduction through two conductivities | T-drop split ∝ L/k |
| frequency / mass | total mass = Σ ρᵢVᵢ; fixed-free two-material bar frequency vs. single-material limit |
| gravity weight | Σ nodal force = Σ ρᵢ g Vᵢ |
| plasticity in one part only | yielding part has plastic strain, elastic part none; global equilibrium |
| thermal strain with different α per part | free expansion of each part = α_i ΔT L_i |
| API: assign material, move, delete (remap), append, import/export | ids remapped, setup preserved, analysis result unchanged |
| `.inp` export → import round trip | parts, materials and sections recovered |
| legacy project | opens as one part, results identical to before |


## 7. Results (what was built and verified)

### Delivered
- **Engine:** `MaterialMap` (per-element materials) used by mechanics (elastic, thermal strain, J2 plasticity per material),
  mass, conductivity, heat capacity, gravity/centrifugal (per-element density) and heat-flux recovery. A single `Material` still works everywhere.
- **Ties rewritten:** slave nodes are interpolated with the master face's **own shape functions** (tri3/tri6/quad4/quad8), so quadratic
  faces pass the patch test exactly (the earlier corner-only interpolation was 5% off for Tet10/Hex20 — found by the new tests).
  Ties also apply to **temperature** (`ConstraintSystem.scalar_T`), so heat flows between tied parts in steady, transient and
  thermo-mechanical analyses. A support that lies on a tied (slave) node is accepted when it is redundant (e.g. a symmetry plane running across both parts).
- **Tie between parts:** `{type: "tie", master_part, slave_part, tolerance?}` auto-detects interface nodes; nodes beyond the tolerance are skipped,
  and an error explains it when none tie.
- **Data/API:** `materials`, `parts`, `surface_parts` columns (idempotent migration; legacy projects open as one part `p0 "Body"`);
  endpoints for material CRUD, part rename / re-assign / move / delete, append with name + material; project file and `.inp` round trips.
- **`.inp`:** export writes one `*ELSET`, `*MATERIAL` and `*SOLID SECTION` per part; import reads element sets, materials
  (`*ELASTIC`, `*DENSITY`, `*EXPANSION`, `*CONDUCTIVITY`, `*SPECIFIC HEAT`, `*PLASTIC`) and sections back into parts and library entries.
- **Delete part:** the mesh is compressed and node ids in BCs, loads, constraints, sets and history nodes are remapped; items that become empty
  or that referenced the part are dropped.
- **UI:** *Parts* panel (colour swatch, rename, material, hide/show, move, delete), *Materials* library editor (new/rename/edit/delete, "used by"),
  per-part tint in the viewport, hidden parts excluded from drawing/picking, material + name chosen when adding a body, *Tie between parts* in Constraints.

### Verification (automated; 111 tests pass on SQLite and PostgreSQL 16)
| Test | Result |
|---|---|
| Two bars, E₁ ≠ E₂ (ν = 0), tied, uniaxial load — Tet4, Tet10, Hex8, Hex20 | **exact** (δ = σ(L₁/E₁+L₂/E₂), rel. 1e-7), reaction exact |
| Same with non-matching meshes (2 vs 3 divisions) | within 0.05–0.7 % (first-order accuracy of one-way ties; master = the *finer* mesh is better) |
| Re-assigning one part's material | answer changes to the new closed form |
| Series conduction across tied parts (k₁ ≠ k₂) | interface temperature exact (matching) / 2 % (non-matching) |
| Transient conduction across a tie → steady limit | within 0.2 % |
| Mass / gravity with two densities | Σρᵢ Vᵢ and Σρᵢ V g exact (1e-9) |
| Free thermal expansion with two α | each body expands by α_i ΔT L_i exactly, zero stress |
| One part plastic, one elastic | plastic strain only in the yielding part; equilibrium exact; elastic-only nonlinear run equals the linear one |
| Equal materials split into groups | identical to the uniform model (u, σ, frequencies to 1e-10) |
| Gap between parts / no tie / bad part data | clear error messages, no crash |
| API: append, rename, re-assign, translate, delete (+ remap), library CRUD, file round trips, legacy project, other users | pass |

### Limits worth knowing
1. **Plasticity is 3-D:** J2 flow is volume preserving, so a yielding part contracts laterally while an elastic neighbour does not; the true field is non-uniform
   (this is physics, not a bug — the 1-D closed form does not apply across such an interface).
2. Ties are **one-way node-to-surface**: exact for matching meshes, first-order for non-matching; choose the finer / stiffer part as master.
   Tie nodes cannot also carry a support unless the support is redundant (see above).
3. Parts are stored as **contiguous element ranges**; imported files whose sets are not contiguous fall back to element blocks (or one part).
4. Parts that **share nodes** (conforming imported meshes) are not tied and cannot be moved on their own (409).
5. Not included: per-part sections (all solids), orthotropic/temperature-dependent materials, part transforms other than translation, surface contact (next step — this change is its prerequisite).
