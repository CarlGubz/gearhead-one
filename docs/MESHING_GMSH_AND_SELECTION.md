# Bricks for every shape, the full Gmsh mesher, quadratic picking and a clearer selection highlight

This change set answers four requests:

| # | Request | Result |
|---|---|---|
| 1 | *Linear brick is not implemented — only tetra. Make it all available, even the remaining ones.* | **Hex8 and Hex20 now work for every primitive** (box, cylinder, sphere, tube, plate with hole, L-bracket, plus the new **cone** and **torus**), for **CAD files** and for **Gmsh `.msh` files**. Every shape × every element type (Tet4, Tet10, Hex8, Hex20) is generated and tested. |
| 2 | *Quadratic should have the cursor in the centre of the line.* | The node pick cursor now snaps to the **mid-side node at the centre of each edge** of a Tet10 / Hex20 mesh, and the click selects exactly that node. |
| 3 | *Do all the mesh capability of Gmsh.* | A **Gmsh mesher** next to the built-in one: every primitive, STEP / IGES / **BREP** / STL files and existing `.msh` files (any version, ASCII or binary, first or second order), with the algorithm, size, recombination, optimisation, refinement and **local size-field** options Gmsh offers (full list in §3). |
| 4 | *Fix the node selection highlight since it is not intuitive.* | Selected nodes are **3-D markers**, selected faces are tinted **exactly** (never bleeding onto neighbours) and outlined, the face under the cursor is **previewed before you click**, and one click selects **one node** — the one the cursor shows. |

Everything is covered by automated tests (§7) and the viewport changes were exercised in a real browser (§7.3).

---

## 1. Bricks (Hex8 / Hex20) for every shape

### 1.1 What was wrong
`meshgen.generate` raised *"cylinder cannot be meshed with hexahedra; choose a tetrahedral element type"* for every curved shape; the UI greyed the brick types out for them; CAD import produced Tet4 / Tet10 only; the `.msh` reader knew Hex8 but not Hex20.

### 1.2 The built-in generators (`backend/app/fea/meshgen.py`)
Curved shapes are meshed as **mapped structured grids**: a logical `(i, j, k)` grid of node positions turned into bricks by `_structured_hex` (an axis can be periodic, which closes rings and the torus). No node is collapsed onto an axis, so every brick has a positive Jacobian.

| Shape | Brick topology |
|---|---|
| box, L-bracket | Cartesian grid (unchanged) |
| cylinder, **cone** | square → disc *elliptical grid mapping* `(s,t) → (s√(1−t²/2), t√(1−s²/2))`, swept along z (the cone scales the radius with height) |
| sphere | cube → ball mapping `x√(1−y²/2−z²/2+y²z²/3)` (and cyclic): the six cube faces land on the sphere |
| tube | polar rings: radial × around (periodic) × height |
| plate with hole | an **O-grid**: the outer rectangle boundary (its four corners are nodes) is joined to the hole by straight radial lines at the same angle |
| **torus** | the disc cross-section swept around the major circle (periodic) |

Tetrahedra of the new shapes (cone, torus) come from the same brick grid split into six tets per brick (Freudenthal pattern, conforming between bricks). Cylinder / sphere / tube / plate-with-hole **keep their Delaunay tetrahedra** — nothing that worked before changed.

`divisions` (the density slider): across the diameter of a cylinder / cone / sphere / torus section there are `2 × divisions` bricks; a tube has `4 × divisions` bricks around; a plate has `2 × divisions` across its narrow side.

### 1.3 Quadratic bricks and mid-side nodes on the true surface
`to_quadratic` adds mid-side nodes on straight edges. `project_to_surfaces` then moves every **boundary mid-side node whose two end nodes lie on the same curved surface** (cylinder, cone, sphere, torus) onto that surface. A mid-node of an interior edge is never touched.

On a very coarse mesh a full move can fold an element (the chord is long compared with the element). The projection therefore **backs off**: nodes of a folded element move only half as far, repeatedly, until every Jacobian is valid again. Result: every shape × every element type meshes at densities 1, 2, 3 … (regression test `test_very_coarse_curved_meshes_stay_valid`).

`ensure_positive_orientation` (`model.py`) now also repairs **inside-out bricks** (it mirrors a brick through its mid-plane, with the right node permutation for Hex8 and Hex20). A brick whose Jacobian changes sign inside is genuinely distorted and is still rejected with a clear message.

### 1.4 Measured accuracy (density 4; error of the meshed volume against the exact volume)

| shape | Tet4 | Tet10 | Hex8 | Hex20 |
|---|---|---|---|---|
| box | +0.00 % (1920) | −0.00 % (1920) | +0.00 % (320) | +0.00 % (320) |
| cylinder | −4.51 % (144) | −0.02 % (144) | −0.66 % (256) | −0.00 % (256) |
| sphere | −28.50 % (240) | −1.12 % (240) | −1.67 % (512) | −0.01 % (512) |
| tube | −4.51 % (576) | −0.02 % (576) | −2.55 % (1280) | −0.00 % (1280) |
| plate with hole | +0.30 % (1656) | +0.30 % (1656) | +0.03 % (192) | +0.00 % (192) |
| L-bracket | +0.00 % (2688) | −0.00 % (2688) | +0.00 % (448) | +0.00 % (448) |
| cone | −0.66 % (1536) | −0.00 % (1536) | −0.66 % (256) | −0.00 % (256) |
| torus | −1.79 % (9216) | −0.01 % (9216) | −1.79 % (1536) | −0.01 % (1536) |

(element counts in brackets; linear meshes under-fill curved shapes, quadratic meshes with surface-fitted mid-nodes do not. The coarse Tet4 sphere is the original Delaunay generator.)

**Solver check on a curved brick mesh:** uniform tension on a cylinder reproduces `uz = σL/E` to 1e-7 and a constant stress field for **all four element types** (the isoparametric patch test), both at engine level and through the HTTP API (`test_curved_bricks_and_tets_pass_the_uniaxial_patch_test`, `test_brick_cylinder_solves_like_the_closed_form_bar`).

### 1.5 Limits
- The corner bricks of the **sphere** (where the cube mapping is most skewed) are the worst elements: linear min Jacobian ratio ≈ 0.5, Hex20 ≈ 0.05 (still valid; use more divisions for stress at the corners). Smoothing was tried and made them worse, so the plain mapping is kept.
- Linear meshes under-fill curved surfaces (table above); use the quadratic types for geometry-sensitive results.
- The primitive mesh is limited to `MAX_ELEMENTS = 60 000` (unchanged).

---

## 2. The two new primitives
`cone` (`bottom_radius`, `top_radius`, `height`; the top radius must be > 0, so it is a frustum) and `torus` (`major_radius`, `minor_radius`, with `minor < major`). Both exist in the built-in mesher, in the Gmsh mesher, the UI, the API (`/api/capabilities` → `shapes`, `shape_defaults`) and the AI assistant's `create_solid_mesh` tool.

---

## 3. The Gmsh mesher

### 3.1 Architecture
```
 API (main.py)  ──▶  cad.py  ──subprocess──▶  gmsh_worker.py   (own process, 180 s limit)
 request options      validate with            builds the geometry, meshes, converts node order,
                      gmsh_options.py          returns nodes / elements / parts / element type
```
- `gmsh_options.py` — option validation and the catalogue the UI builds its form from. It does **not** import gmsh, so the API process stays free of the geometry kernel.
- `gmsh_worker.py` — replaces `cad_worker.py`. One job meshes one of three **sources**: a `primitive` (OpenCASCADE solid built from the dimensions), a `file` (STEP / IGES / BREP / STL) or an existing `msh` mesh.
- `cad.py` — `mesh_primitive`, `mesh_cad`, `mesh_msh`; returns a `GmshResult` (nodes, elements, per-solid ends, element type, info).
- A crash or hang of the kernel cannot take the API down; failures come back as one readable line.

### 3.2 Element families and strategies
| family / order | element | how |
|---|---|---|
| tet / 1, 2 | Tet4, Tet10 | volume mesh (`Mesh.Algorithm3D`), second order with `setOrder(2)` |
| hex / 1, 2 | Hex8, Hex20 (serendipity, `Mesh.SecondOrderIncomplete`) | **sweep** — a recombined quad mesh of a 2-D profile extruded along z with `recombine=True` (box, cylinder, tube, plate with hole, L-bracket) — or **subdivide** — a tet mesh split into four bricks per tetrahedron (`Mesh.SubdivisionAlgorithm=2`; used for sphere, cone, torus and every CAD file). In subdivide mode the tet size is doubled so the bricks keep the requested size. |

Gmsh node orders are converted to the solver's Abaqus order (`_TET10`, `_HEX20` in the worker). A test builds a straight-edged box and asserts that **every mid-side slot sits at the centre of the edge it names**, so a wrong permutation cannot slip through.

Safety nets: a curved quadratic (or subdivided) mesh whose Jacobian changes sign anywhere is **rebuilt with straight edges**; sliver tetrahedra of STL geometry are dropped; elements the solver cannot use (prisms, pyramids — e.g. a swept mesh whose quad recombination left triangles) produce an explanatory error instead of a broken model.

### 3.3 Options (all validated server-side; defaults in `gmsh_options.DEFAULTS`)
| option | Gmsh setting | notes |
|---|---|---|
| `family`, `order` | element type | derived from the UI's element-type selector |
| `size`, `min_size`, `size_factor` | `Mesh.MeshSizeMax/Min/Factor` | `size = 0` → smallest dimension ÷ density (primitives) or diagonal ÷ 12 (files) |
| `curvature_points` | `Mesh.MeshSizeFromCurvature` | elements per 2π |
| `size_from_points`, `extend_from_boundary` | `Mesh.MeshSizeFromPoints/ExtendFromBoundary` | |
| `algorithm_2d` | `Mesh.Algorithm` | MeshAdapt, Automatic, Delaunay, Frontal-Delaunay, Frontal-Delaunay for quads |
| `algorithm_3d` | `Mesh.Algorithm3D` | Delaunay, Frontal, MMG3D, R-tree, HXT |
| `recombine_algorithm` | `Mesh.RecombinationAlgorithm` | simple / blossom / full-quad variants (swept bricks) |
| `hex_method` | – | `auto`, `sweep`, `subdivide` |
| `smoothing_steps` | `Mesh.Smoothing` | |
| `optimize` | `mesh.optimize` | Netgen, Relocate 3-D, Untangle |
| `refine` | `mesh.refine` | uniform refinement passes (0–2) |
| `straight_midside` | `Mesh.SecondOrderLinear` | |
| `curve_optimize` | `Mesh.HighOrderOptimize` | high-order, elastic, fast curving |
| `regions` | `mesh.field` Ball / Box + Min | up to 8 local **size fields** (smaller elements inside a ball or box, growing back over a transition thickness) |

Deliberately **not** offered, because they were tested and do not work: BAMG (fails on every curved OpenCASCADE surface), *Packing of parallelograms* (crashes the kernel) and Gmsh's experimental 3-D recombination (produced no bricks). `.geo` scripts are not accepted either (they can run system calls).

### 3.4 API
| endpoint | change |
|---|---|
| `POST /mesh/primitive`, `POST /mesh/append` | new `mesher: "builtin" \| "gmsh"` and `gmsh: {…options}`. With Gmsh the element type follows `gmsh.family/order`, or `element_type` when they are omitted. *Add body* always takes the model's element type. |
| `POST /mesh/cad` | new form fields `family` (`tet`/`hex`) and `options` (JSON string with any Gmsh option); `.brep` / `.brp` added to `.step .stp .iges .igs .stl`; result is Tet4 / Tet10 / Hex8 / Hex20 |
| `POST /mesh/import` | `.msh` of any Gmsh version, ASCII or binary, linear or quadratic (the built-in reader still handles 2.2 ASCII, now incl. Hex20; everything else falls back to Gmsh); one part per volume |
| `GET /api/capabilities` | `shapes`, `shape_defaults`, and `gmsh: { available, version, options (catalogue), sweepable_shapes }` |

Errors are always `400` with the reason (bad option, geometry that cannot be swept, element size too small, kernel failure) and **leave the project untouched**; `501` if Gmsh is not installed.

### 3.5 UI (Geometry & Mesh panel)
All eight primitives, all four element types (nothing is greyed out any more), a **Mesher** selector (Built-in / Gmsh), and a collapsible **Gmsh options** form (`GmshOptions.jsx`) with the groups Size · Algorithms · Quality · Local refinement. CAD import has its own element family / order selectors and the same options form, and accepts `.brep`. The AI assistant can mesh cones and tori, choose bricks for any shape and select the Gmsh mesher.

### 3.6 Limits
- Swept bricks exist for the five extrudable shapes; everything else (and every CAD file) is subdivided tetrahedra, which gives more, smaller bricks and mediocre corner quality (min Jacobian ratio about 0.2 for Hex8 and 0.05–0.1 for Hex20 on sphere / cone / torus — valid, but not as good as a swept mesh).
- Hex27 meshes are reduced to Hex20; prisms, pyramids and mixed meshes are rejected with a message.
- The 180 s kernel limit and the 25 MB upload limit are unchanged.
- Gmsh is GPL-2+ with an OpenCASCADE linking exception (see `DEPLOYMENT.md`).

---

## 4. The pick cursor on quadratic meshes

### 4.1 Problem
The viewport draws corner-only triangles, so the cursor could only ever snap to **corner** nodes; the mid-side nodes of Tet10 / Hex20 (the ones at the *centre of each edge*) could not be picked or even seen.

### 4.2 Data: `surface_mid`
For every render triangle the project now carries the three mid-side nodes of its edges (`c0–c1`, `c1–c2`, `c2–c0`; `-1` where there is none — linear meshes, and the diagonal of a quad face).

- `FEMesh._triangle_midnodes` builds it from the boundary face lists with one sorted-key lookup (the face orientation flip is handled by matching edges, not positions).
- `projects.surface_mid` is a new JSON column (**auto-migrated** at start-up), included in undo/redo snapshots and in `ProjectOut`. It is empty for linear and structural models.
- Projects saved before this change and old undo snapshots rebuild it on demand (`schemas._legacy_surface_mid`), so nothing needs a manual migration.

### 4.3 Viewport
`nearestNode` in `Viewport3D.jsx` considers the three corners **and** the mid-side nodes of the triangle under the pointer and snaps to the nearest, using the node's drawn (deformed) position. The PrePoMax-style hub-and-arrows cursor therefore sits exactly **at the centre of the line** between two corner nodes. In node mode every pickable node (corners and mid-side nodes) is drawn as a small dot so the snapping targets are visible. The cursor size now follows the mesh size instead of the model size.

---

## 5. Selection highlight

### 5.1 What was wrong
- A click in node mode selected **all three nodes of the triangle** under the pointer although the cursor showed **one** node.
- The highlight painted every vertex of those nodes in *every* triangle that shared them, so the magenta **bled across neighbouring faces** as gradients and did not match what was selected.
- Selected faces were highlighted the same way, so a brick face (two triangles) looked like a ragged half-quad.

### 5.2 Now
| | behaviour |
|---|---|
| **Node tool** | one click = **one node**, the one the cursor is snapped to (a corner or, on quadratic meshes, a mid-side node). Shift adds a node, or removes it if it is already selected. A plain click on the only selected node clears it. |
| **Node highlight** | each selected node is a **magenta 3-D sphere** sized to the mesh; hidden when the node is on the far side of the model; drawn on top for beams / bars (whose nodes sit inside the drawn tube); follows the deformation. |
| **Face tool** | the face under the cursor is **previewed** in amber before you click; a click selects it (shift adds / removes). On brick meshes and S4 shells the preview and the selection are the **whole quadrilateral** (both triangles). |
| **Face highlight** | exactly the selected triangles are tinted magenta and the **outline** of the selection is drawn; neighbours are untouched. |
| Angle tool | unchanged, plus shift now adds to the selection. |

The Selection panel text and the toolbar tooltips describe the new behaviour.

### 5.3 Behaviour changes to be aware of
- In node mode the old "click a face to select its three nodes" no longer exists; to constrain or load a whole face use the **face tool**, the **By coordinate** tool in the Selection panel, or select several nodes with shift.
- Loads and supports applied to **two corner nodes of one edge** of a quadratic mesh still get that edge's mid-side node automatically (`FEMesh.expand_selection`, unchanged); a mid-side node can now also be selected on its own.

---

## 6. Files

| area | files |
|---|---|
| mesh core | `fea/meshgen.py` (bricks for all shapes, cone, torus, surface projection with back-off), `fea/model.py` (`tri_mid`, brick orientation repair), `fea/inp.py` (`.msh` Hex20) |
| **new** Gmsh | `fea/gmsh_options.py`, `fea/gmsh_worker.py` (replaces `fea/cad_worker.py`), `fea/cad.py` (rewritten) |
| API | `main.py` (mesher selection, CAD options, `.msh` fallback, `surface_mid`, capabilities), `schemas.py`, `models.py`, `history.py`, `agent.py` |
| UI | `components/Viewport3D.jsx` (snapping, markers, tint, outline, hover, dots), `components/GeometryPanel.jsx`, **new** `components/GmshOptions.jsx`, `pages/Workspace.jsx`, `components/SelectionPanel.jsx`, `api.js`, `styles.css` |
| tests | **new** `tests/fea/test_hex_everywhere.py`, `tests/fea/test_gmsh_mesher.py`, `tests/test_meshing_api.py`; `tests/test_simulation_api.py` updated (a cylinder *can* now be meshed with bricks) |
| docs | this file, `README.md`, `SOLVER_REFERENCE.md` §7, `SOLVER_AND_PREPOMAX_PLAN.md`, `NLGEOM_STRUCTURAL_CAD.md` §5.3, `DEPLOYMENT.md` |

---

## 7. Verification

### 7.1 Automated (`cd backend && pytest tests`)
The suite grew from 229 to **459 tests, all passing**. New coverage:

- **every primitive × every element type** (32 combinations): right-handed, volume within tolerance, no inverted elements, no orphan nodes; bricks watertight; mid-side nodes of cylinders / tubes / cones / holes / spheres / tori lie on the exact surface; densities 1 and 2 stay valid;
- the **uniaxial patch test** on a curved cylinder for Tet4, Tet10, Hex8 and Hex20, and through the API;
- **`surface_mid`**: aligned with the triangles, mid-nodes at the centre of straight edges, only quad diagonals have none, follows the orientation flip, survives undo / redo / add body / delete body / save-open, rebuilt for legacy rows;
- **Gmsh**: option validation (22 rejected inputs), every primitive as quadratic tet and hex, sweep vs subdivide, node-order test, every 2-D / 3-D algorithm and recombination mode, optimisers, refinement, straight mid-side, curvature sizing, **local size fields** (elements near the ball have ≥ 10× less volume than the far ones), CAD files (STEP, BREP) as bricks, `.msh` v2.2 / v4 ASCII / v4 binary / second order / Hex20, unsupported and corrupt files, API errors that leave the project untouched.

### 7.2 Gmsh results (density 4; volume error against exact, elements, method)

| shape | Tet4 | Tet10 | Hex8 | Hex20 |
|---|---|---|---|---|
| box | +0.00 % (1868) | −0.00 % (1868) | +0.00 % (368) sweep | +0.00 % (368) sweep |
| cylinder | −1.34 % (981) | −0.00 % (981) | −1.64 % (144) sweep | −0.00 % (144) sweep |
| sphere | −3.48 % (665) | −0.02 % (665) | −0.78 % (2660) subdivide | +0.03 % (2660) subdivide |
| tube | −0.00 % (19718) | +0.00 % (19718) | +0.01 % (5420) sweep | +0.00 % (5420) sweep |
| plate with hole | +0.05 % (15197) | +0.00 % (15197) | +0.07 % (4100) sweep | +0.00 % (4100) sweep |
| L-bracket | +0.00 % (2489) | −0.00 % (2489) | +0.00 % (528) sweep | +0.00 % (528) sweep |
| cone | −2.24 % (555) | −0.00 % (555) | −0.50 % (2220) subdivide | +0.02 % (2220) subdivide |
| torus | −3.00 % (2904) | −0.01 % (2904) | −2.52 % (1904) subdivide | +0.09 % (1904) subdivide |

### 7.3 Browser run (Chromium driven over the DevTools protocol against the real backend)
- **Quadratic brick box:** hovering the centre of an edge puts the cursor exactly there (x = 0.05 m, where only a mid-side node exists) and a hover a few pixels off still snaps to it; one click → one marker; shift-click adds two more (three markers 0.05 m apart); shift-click on one removes it. Face tool: the whole quad is previewed, clicking selects it with an outline, and the earlier node markers are cleared.
- **Beam model:** markers are visible on top of the thin tube; two nodes selected.
- **Quadratic cylinder (Tet10):** mid-side nodes are drawn on the true curved surface (slightly outside the flat-shaded outline) and are pickable.
- **Meshing UI:** the primitive list shows all eight shapes with every element type enabled; a built-in Hex8 torus (1 944 nodes, 1 536 elements) and a Gmsh Hex20 cone with a refinement ball (368 elements) generated from the panel; the Gmsh options form renders and edits.
- No console errors other than the pre-existing React-Router deprecation notices.

### 7.4 Not verified
- Docker images were not rebuilt; the image already installs `gmsh`, nothing new is required.
- PostgreSQL: the new `surface_mid` column is added by the same idempotent migration that covers the other JSON columns (exercised on SQLite).
- Quad-face picking was checked in the browser on Hex20 boxes; S4 shells use the same sibling rule (triangle *i* and *i + N/2*) but were not clicked through.


## Box selection (drag a rectangle)
`Viewport3D.jsx` (`boxPick`) · `Workspace.jsx` (`handleBoxSelect`)

- Active in the node tool and the face tools. A press that moves more than 4 px becomes a box; a shorter press stays a click.
- **Window / crossing** (CAD convention): dragging left → right selects only what lies fully inside (solid blue box);
  right → left selects anything with a corner inside (dashed green box). Nodes are points, so both modes agree for nodes.
- **Shift** adds, **Ctrl** (⌘ on macOS) removes, no modifier replaces. Esc cancels a drag.
- **Visible side only** by default: a surface triangle counts only if it faces the camera (back-face test on the
  deformed shape). **Through** in the viewport toolbar switches this off. Shells (S4) and beam / truss tubes are always
  selected through, as their faces have no inside / outside. Nodes or faces cut away by the section plane are skipped.
- Node mode takes the corner nodes and, on quadratic meshes, the mid-side nodes of the qualifying triangles; on line
  models the tube vertices map back to the element nodes. Face mode keeps the two triangles of a brick quad together.
- While a selection tool is active, left-drag belongs to the box: the view rotates with the right button and pans with the
  middle one (wheel zoom unchanged). The orbit tool keeps the normal mapping.
- Limits: "visible side" is a facing test, not an occlusion test, so on strongly concave parts a face that faces you but is
  hidden behind another part of the model can still be taken. Interior nodes of solids are never box-selected.
