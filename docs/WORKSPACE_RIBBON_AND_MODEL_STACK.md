> **Gearhead suite note.** The *Functions* switcher now picks the **FEA · CFD · CAD** workspaces of a project (see the
> README, section 2). The CAD / CAM / CAE sub-workspaces described in section 2 below were merged into the FEA ribbon:
> every FEA command is always available and the CAM *Setup* command is called **Stock**. The rest of this document
> (ribbon, dialogs, left panel, model stack, API) still describes the FEA workspace.

# Workspace layout: command ribbon, CAD / CAM / CAE functions and the model stack

This change reorganises the simulation workspace (`/app`) and adds multi-model project files.

```
┌ app bar ─ logo · project · active model · AI Assistant · Guide · account ──────────────────────────────┐
├ ribbon ─ [Functions ▾] │ Project▾ │ Undo Redo │ Geometry▾ Frames▾ │ Parts▾ Materials▾ Select▾ │ Solve ──┤
├────────────────────────────────┬───────────────────────────────────────────────────────────────────────┤
│ [Geometry][Model][Analysis]    │                                                                       │
│ [🔍 Search the tree         ]  │                         3-D viewport                                   │
│ [-] Results                    │   (command dialogs open at its top-left corner; visible inactive      │
│  ┊ [+] Runs (2)                │    models are drawn as faint reference ghosts)                        │
│  ┊ [-] Field Outputs (4)       │                                                                       │
│  ┊  ┊ [-] DISP                 │                                                                       │
│  ┊  ┊  ┊ ALL · U1 · U2 · U3    │                                                                       │
└────────────────────────────────┴───────────────────────────────────────────────────────────────────────┘
```

## 1. Command ribbon (top menu)
The former left-hand menu (Project, Geometry & Mesh, Beams/trusses/shells/springs, Parts, Materials, Selection & sets)
is now a Fusion 360-style ribbon (`components/Ribbon.jsx`). Every command has its own icon (`components/icons.jsx`, inline
SVG that follows the theme) and a label with a **▾ arrow**. Clicking the icon or the arrow opens the command's dialog
(`components/CommandModal.jsx`) holding the same sub-options as before (the panels themselves are unchanged).

Dialogs behave like Fusion 360 command dialogs:
- they float above the viewport and can be dragged by the title bar; **Esc** or ✕ closes them;
- they do **not** block the workspace, so you can keep picking nodes / faces in the viewport while *Select* is open;
- they stay mounted while closed, so a half-filled form (e.g. CAD import options) survives closing and reopening;
- only one is open at a time; clicking the open command again closes it.

*Undo* and *Redo* (right of Project, left of Geometry; tooltips name the step, Ctrl+Z / Ctrl+Y still work) and *Solve* are direct actions (no dialog). Ribbon badges show the number of parts and the current selection size.

## 2. Multiple Functions: CAD / CAM / CAE
The button at the left end of the ribbon switches workspace, which decides the commands offered:

| Workspace | Ribbon | Analysis tab |
|---|---|---|
| **CAD · Design** | Project · Undo, Redo · Geometry, Frames · Parts, Materials | results tree only (+ a button to switch to CAE) |
| **CAM · Manufacture** | Project · Undo, Redo · Setup (stock) · Toolpath, Simulate, Post (*planned, disabled*) | results tree only (+ a button to switch to CAE) |
| **CAE · Simulation** | Project · Undo, Redo · Geometry, Frames · Parts, Materials, Select · Solve | Analysis type, Supports & loads, Constraints, Solve & display, Results |

Each model of the stack remembers the workspace it belongs to (its `process`: `cad`, `cam` or `cae`, shown as a coloured
tag in the Model panel); activating a model switches to its workspace, and switching workspace re-tags the active model.

**CAM status:** only *Setup* works today — it computes the stock block around the active model (bounding box plus a
machining allowance per side), its origin, its volume and the material to remove. Toolpath generation, machining
simulation and post-processing are **not implemented**; their buttons are disabled and say so.

## 3. Left panel (PrePoMax style)
One panel (320 px) left of the viewport, modelled on PrePoMax's tree panel (`SidePanel` in `components/BrowserPanel.jsx`):
- **Tabs** across the top: **Geometry | Model | Analysis**. The last tab used is remembered in the browser. All tabs stay
  mounted, so forms keep their values when you switch.
- A **search box** filters the active tab's tree as you type: a node stays visible when its label matches, when one of
  its children matches, or when it sits under a matching node (searching "Parts" lists every part). Esc clears it.
- **Trees** drawn like PrePoMax: [+]/[−] boxes, dotted connector lines, an icon per node and "(n)" counts.
- When the Guide or a link points at a section in another tab (for example *Supports* in the Model tree), the panel
  switches to that tab first.

Command dialogs open at the viewport's top-left corner so they never cover the panel (once dragged, a dialog stays where
it was put).

### Geometry tab (`components/GeometryBrowser.jsx`)
Laid out like PrePoMax's Geometry tree (roots *Geometry* and *Mesh*):
- **Input geometry** — what each body was made from: primitive with dimensions, mesher and density (and offset for added
  bodies), structural frame, CAD file (unit, family, order, size) or imported mesh file. A CAD file with several solids
  is one input that produced several parts.
- **Output mesh** — element type, node / element counts, every mesh part (colour swatch, element and node count, material,
  eye button to hide / show) and the mesh statistics.

The server records the input on each part as `parts[i].source` when the part is created (primitive, add body, frames,
mesh import, CAD import, custom upload). Parts created before this change show "source not recorded".

### Model tab (`components/ModelStack.jsx`) — several models in one file
A project now holds a **stack of models**, ordered like layers in an image editor (newest on top). Each model is a full
model: its own mesh and parts, materials, supports, loads, constraints, sets, analysis settings, **undo history** and
**results**.

- Click a model to make it the **active** one; every command, the solver and the AI assistant work on the active model.
- Toolbar: **+** new empty model · **⧉** duplicate the active model (mesh and setup, not results or history) ·
  **↑ / ↓** reorder · **🗑** delete (a project keeps at least one model).
- Double-click a name to rename it.
- **Eye**: a visible inactive model is drawn in the viewport as a faint, non-pickable reference ghost (handy to compare
  two variants in place). The active model is always visible.
- **The active model's tree**, in PrePoMax's FE Model layout: *Mesh → Parts (n), Node Sets (n) · Features → Reference
  Points (n) · Materials (n) · Constraints (n) · Steps → Step-1 · <analysis> → Supports (n), Loads (n)*, each listing its
  items by name; clicking an item opens the matching command dialog or highlights the matching section.
- **History**: the active model's edit history (Initial, Generate mesh, Add support…); click an entry to jump straight
  to that state (states after the current one are shown in italics and can be redone the same way).

### Analysis tab
Holds what used to be the right-hand sidebar — **Analysis type** (and its settings, gravity, centrifugal load),
**Supports & loads**, **Constraints**, **Solve & display** (run button, deformation scale, legend range, section plane,
probe, export), each drawn as a tree row — followed by the **Results** tree. There is no right sidebar. Outside the CAE
workspace the tab shows only the results tree and a button to switch to CAE.

#### Results tree (`components/ResultsBrowser.jsx`)
**Results → Runs** (the last 20 runs of the active model; click one to load
its results), then **Step-1 · <analysis>** with **Field outputs** (click to contour), **Modes / buckling factors** or
**Time frames**, **History Outputs**, **Summary** and **Export** (CSV / VTK / PNG). Outside CAE it has its own
**Run analysis** button.

## 4. Server side
### Storage
- `projects` columns still hold **the active model**, so all existing endpoints, the solver and the AI assistant are
  unchanged. New column `projects.active_layer_id`.
- New table `model_layers` (`ModelLayer`): `id, project_id, position (0 = bottom), name, process, visible, summary,
  data, created_at, updated_at`. `data` is a zlib-compressed JSON snapshot of the model fields (`history.SNAP_FIELDS`)
  plus that model's `history_pos` / `history_labels`; it is `NULL` for the active model.
- Activating a model stashes the active one into its row and loads the target's snapshot into the project columns.
- `project_history.layer_id` and `jobs.layer_id` tag undo snapshots and runs with their model, so undo / redo,
  "latest results" and the runs list are per model. A solve snapshots the model when it is requested, so switching
  models while it runs is safe.
- Migration is automatic on startup (`_migrate_schema` adds the columns, `create_all` the table). A project created
  before the stack becomes a one-model stack ("Model 1") the first time it is opened; its existing history and results
  are adopted by that model. Logic lives in `backend/app/layers.py`.

### API
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/projects/{id}/layers` | New model on top `{name, process, duplicate_of?}`; becomes active |
| `PUT` | `/api/projects/{id}/layers/{lid}` | Rename / re-tag / show-hide `{name?, process?, visible?}` |
| `POST` | `/api/projects/{id}/layers/{lid}/activate` | Make a model the active one |
| `POST` | `/api/projects/{id}/layers/{lid}/move` | `{direction: "up" \| "down"}` |
| `DELETE` | `/api/projects/{id}/layers/{lid}` | Delete a model with its history and runs |
| `GET` | `/api/projects/{id}/layers/{lid}/preview` | Nodes / surface triangles / line segments of a model (viewport ghost) |
| `POST` | `/api/projects/{id}/history/{seq}` | Jump to one state of the active model's history |
| `GET` | `/api/projects/{id}/jobs/summary` | Last 20 runs of the active model without result arrays |

Project responses gain `active_layer_id` and `layer_stack` (top first: `id, name, process, visible, position, active,
summary, updated_at`); job responses gain `layer_id`. `GET …/jobs` and `GET …/jobs/latest` now refer to the active model.

### Project files
*Save project* writes **version 2**: the top-level keys are still the active model (so older readers open it) and
`layers` holds the whole stack, bottom first: `[{name, process, visible, active, model: {…}}]`. *Open* accepts version 1
(single model) and version 2 files. At most 20 models per project.

## 5. Verification
- `backend/tests/test_layers_api.py` (9 tests): switching keeps each mesh and setup, per-model undo, history jumps,
  duplicate / rename / move / hide / delete, per-model results and the light runs list, file round trip of the whole
  stack (and version-1 files), part input geometry, adoption of pre-stack projects. The full suite passes.
- The startup migration was run against a database with the pre-change schema.
- The UI was driven end to end in headless Chromium: create project from the ribbon dialog, mesh, add a second model,
  ghost display, switch models, CAD / CAM / CAE switcher, CAM setup, solve → Results tree → contour, history jump, Guide
  opening ribbon dialogs, light and dark themes — with no console errors.

## 6. Limits
- CAM toolpaths, machining simulation and post-processing are not implemented (Setup only).
- Models in a stack are independent: there is no constraint or contact *between* models (use parts of one model for
  that). Ghosts are for visual reference only.
- Undo history is per model and is not saved in project files (as before).
- Dialog positions are not remembered between page loads.
