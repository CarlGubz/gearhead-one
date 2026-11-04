# AI engineering assistant and 3-D viewport symbols

Two changes to the workspace: (1) support / load symbols and the pick cursor now look like PrePoMax's 3-D glyphs
instead of square points and a ◆ icon; (2) an AI agent turns a plain-language problem statement into a solved model.

## 1. Viewport: glyphs and pick cursor

| What | Before | Now |
|---|---|---|
| Fixed / symmetry / prescribed support | blue square point per node | blue **cone** per restrained axis (x, y, z), sitting outside the body and pointing at the node |
| Force, remote force, traction | red square points | red **3-D arrow** per node / face, tip on the node, pointing the way the load acts |
| Pressure | red points | red arrows normal to each face, pointing **into** the surface for positive pressure (as the solver defines it) |
| Fixed temperature | teal points | teal **sphere** |
| Heat source / flux / convection / radiation | orange points | orange sphere (nodal) or orange arrows into the surface (faces) |
| Point mass | — | grey sphere |
| Gravity | — | one red arrow above the model along the gravity vector |
| Probe, min / max markers | square points | round discs (still follow the deformation animation) |
| Selection tools | `◆ ▤ ◪` text icons | SVG pictograms (orbit, node, face, face-by-angle) |
| Mouse cursor over the model | arrow | crosshair in selection modes, grab hand in orbit mode |
| Hover | none | **3-D pick cursor**: white hub with red X / green Y / blue Z arrows. In node mode it snaps to the nearest node of the triangle under the pointer — corner nodes, and on quadratic meshes also the mid-side node at the centre of each edge; in face modes it follows the surface (and the face under it is previewed). Later changes to picking and the selection highlight: [`MESHING_GMSH_AND_SELECTION.md`](MESHING_GMSH_AND_SELECTION.md) |

Implementation: [`glyphs.js`](../frontend/src/components/glyphs.js) turns a project into *layers*
(`{shape, color, items:[{p, d}]}`) — pure data, no WebGL. [`Viewport3D.jsx`](../frontend/src/components/Viewport3D.jsx)
draws each layer as a single `InstancedMesh` (one draw call per colour/shape however many nodes), so a fixed face with
hundreds of nodes costs nothing. Glyphs are thinned to at most 90 nodes (180 cones) per layer and their size shrinks as a
layer gets crowded. Symbols are drawn on top of the mesh (`depthTest` off) so loads that act from inside stay visible.
Glyphs are hidden while a result contour is displayed, as before.

Limits: moments, line loads and reference-point supports have no symbol yet; outward face normals are oriented away from
the model centre, which is exact for convex parts and approximate for strongly concave ones.

## 2. AI engineering assistant

### Using it
Click **✦ AI Assistant** in the workspace toolbar, type the problem (or click an example) and press Enter. The agent:

1. asks one short question if something essential is missing, otherwise states its assumptions;
2. builds the model with tools (mesh, material, analysis type, supports, loads, gravity);
3. calls `run_simulation`; the browser then starts the job through the **same code path as the Solve button**
   (`runSolve` in `Workspace.jsx`), so the progress bar, messages, results contour and error handling are identical to a
   manual run.

Each tool call is shown under the reply (✓ / ✕ with a one-line detail). All edits are normal project edits: they appear in
the model tree, can be changed by hand and undone with Ctrl+Z. Conversations are kept per project in `sessionStorage`.

### Setup
The assistant is driven by **Google Gemini 2.5 Pro** by default. Put the key in the **backend** `.env` (never in the
frontend):

```
GEMINI_API_KEY=<key from https://aistudio.google.com/apikey>
GEMINI_MODEL=gemini-2.5-pro
```

`.env` is read from `backend/.env` or the repository-root `.env` (the one Docker Compose uses); variables already set in
the real environment win, and empty `KEY=` lines are ignored. Restart the backend after editing it. Without a key for the
selected provider the panel shows that the assistant is off and `POST …/agent/chat` answers 503.

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | — | Gemini key. Setting it is all that is needed to switch the assistant on |
| `GEMINI_MODEL` | `gemini-2.5-pro` | Gemini model id |
| `AGENT_PROVIDER` | empty | `gemini`, `anthropic` or `openrouter`. Empty = the first provider that has a key, in the order Gemini → Anthropic → OpenRouter |
| `ANTHROPIC_API_KEY`, `AGENT_MODEL` | — / `claude-sonnet-5-5` | Alternative provider, used with `AGENT_PROVIDER=anthropic` (or when no Gemini key exists) |
| `OPENROUTER_API_KEY` | — | Last-resort provider ([openrouter.ai/keys](https://openrouter.ai/keys)); used automatically only when neither a Gemini nor an Anthropic key is set |
| `OPENROUTER_MODEL` | `google/gemini-2.5-pro` | Any OpenRouter model id that supports tool calling |
| `AGENT_MAX_TURNS` | 14 | Tool-loop cap |
| `AGENT_REQUESTS_PER_MINUTE` | 12 | Per-user rate limit |

No new Python dependency: all three APIs are called with `httpx`, which was already required.
`GET /api/agent/status` now also returns `provider`.

### Architecture
```
AgentPanel ──POST /api/projects/{id}/agent/chat {messages}──▶ agent.run_agent
                                                              │  loop (≤ AGENT_MAX_TURNS):
                                                              │    LLM (system prompt + live model summary + tools)
                                                              │      └ Gemini 2.5 Pro (default) or Anthropic, via _post_messages
                                                              │    tool call ─▶ ToolBox ─▶ the same handlers the REST API uses
                                                              │    tool result (errors flagged so the model self-corrects)
            ◀── {reply, steps[], run_simulation, project} ────┘
if run_simulation: Workspace.runSolve(project)  ──▶ POST /solve (normal job + polling)
```
* **Gemini integration.** The loop and the tools are provider-independent: the loop speaks Anthropic-style
  `tool_use` / `tool_result` blocks, and `_post_gemini` translates each request to Gemini `generateContent` and each reply
  back (`_gemini_request` / `_gemini_response` in [`agent.py`](../backend/app/agent.py)):
  * system prompt → `systemInstruction`; tools → `functionDeclarations` with the JSON schema passed as
    `parametersJsonSchema` (so `additionalProperties` and `["number","null"]` types survive; tools without parameters send none);
  * assistant `tool_use` → `functionCall` parts, our results → `functionResponse` parts (matched to the call by name), and
    the model's `thoughtSignature` is stored on the call and replayed on the next turn, as Gemini 2.5 expects;
  * reasoning (`thought`) parts are dropped from the reply; a blocked prompt or an empty candidate becomes a readable reply
    instead of an error; `maxOutputTokens` is at least 8192 because Gemini 2.5 Pro's thinking tokens count against it;
  * HTTP 401/403 → 503 (check `GEMINI_API_KEY`), 429 → 429, other errors → 502; the provider's response body is logged
    (`webfea.agent`, never sent to the browser).
* **OpenRouter fallback.** OpenRouter speaks the OpenAI chat-completions format, translated by `_openrouter_request` /
  `_openrouter_response`: system prompt → a `system` message, tools → `{type:"function", function:{name, description,
  parameters}}`, assistant `tool_use` → `tool_calls` (arguments as a JSON string), our results → `role:"tool"` messages keyed by
  `tool_call_id`. Provider selection is `AGENT_PROVIDER` if set, otherwise the first key present (Gemini, Anthropic,
  OpenRouter); `GET /api/agent/status` reports which one is active. Error mapping is the same as for Gemini (401/403 → 503,
  429 → 429, else 502). The chosen OpenRouter model must support tool calling.
* **Hands-off operation.** The system prompt now tells the model to fill every gap itself (steel, static, C3D10, a fixed end
  for a cantilever…) and list those assumptions in the reply; it asks a question only when a dimension or load that decides
  the whole answer is completely missing. After `run_simulation` the browser starts the job through the normal Solve path,
  so the user never types mesh / material / support values and the output (progress, contour, results) is the usual one.
* **The agent never runs the solver.** `run_simulation` only validates the model (mesh present, supports present, a load /
  gravity / prescribed displacement present, a fixed temperature for thermal runs, plan allows the analysis type) and sets a
  flag.
* **Tools**: `inspect_model`, `create_solid_mesh`, `create_structural_model`, `set_material` (presets: steel, stainless,
  aluminium, titanium, copper, cast iron, concrete, or explicit SI values), `set_analysis`, `add_support`,
  `add_temperature`, `add_load` (force, pressure, traction, heat flux, surface flux, body heat, convection, radiation,
  line load, point mass), `set_gravity`, `clear_supports_and_loads`, `run_simulation`.
* **Locating things without node ids.** Supports and loads take a `region`: a bounding-box face
  (`{"type":"face","side":"x_min"}`), a coordinate `box` (null = unbounded side), a `near_point` sphere, or `all`. The server
  resolves it to the surface nodes / whole surface triangles inside it (tolerance 1e-4 of the model diagonal) and the quadratic
  mid-side nodes are added by the solver as for manual selections. A region that matches nothing returns an error the model
  can act on.
* **Units / conventions** are in the system prompt: SI only (the agent converts mm, MPa, kN), Z up, solid primitives
  centred on the origin, positive pressure pushes into the surface.
* **Safety and limits**: the model is told to ask before guessing essentials and never to quote results it has not seen;
  Pro-only analysis types are refused by the same plan check as the UI (the agent reports it and offers static); per-user
  rate limit; history capped at 30 messages × 6000 characters; projects are only reachable by their owner (404 otherwise).
  Whatever the user types is sent to the configured LLM provider (Google Gemini by default), so deployments should mention
  this in their privacy notice.

### Verification
`backend/tests/test_agent.py` drives the real tool loop with a **scripted fake LLM** (no network, no key):
* a full cantilever conversation creates the mesh, material, support and end pressure, requests the run, and the manual solve
  of that model reproduces the closed-form axial shortening −PL/E within 2 %;
* a beam-line structural model built by the agent solves;
* tool errors (no mesh, empty region, nothing to run) come back flagged `is_error` and the loop continues;
* Pro-only analysis is refused for a Free user; foreign project → 404; bad history → 422; loop bounded by `AGENT_MAX_TURNS`;
* unconfigured server → status `available: false` and 503.

Gemini (same file, only `httpx.post` is faked, so the real translation code runs):
* provider selection: Gemini is chosen when its key is set, status reports `provider` / `model`, and forcing
  `AGENT_PROVIDER=anthropic` without an Anthropic key switches the assistant off;
* the cantilever conversation through Gemini replies (parallel `functionCall`s, a `thought` part, a thought signature)
  builds the model, requests the run, and the manual solve gives −PL/E within 2 %;
* the outgoing request: `x-goog-api-key` header, model in the URL, `systemInstruction`, `functionDeclarations`, replayed
  `functionCall` + `thoughtSignature`, `functionResponse` with the tool result, `maxOutputTokens ≥ 8192`;
* tool errors reach the model in `functionResponse`; HTTP 403 / 429 / 500 map to 503 / 429 / 502; a `promptFeedback` block
  gives a readable reply.

OpenRouter: fallback order (OpenRouter only when Gemini and Anthropic keys are empty; an explicit `AGENT_PROVIDER` wins),
the full cantilever conversation through `tool_calls` (solves to −PL/E), the outgoing `messages` / `tools` / `Bearer` header
shape including `tool` result messages, and 401 / 429 / 500 mapping.

What was **not** tested: the live Gemini, Anthropic and OpenRouter APIs (they need a key). The Gemini request/response shape follows the
public `generateContent` function-calling format; if Google rejects a tool schema, the 400 body is in the backend log. The
transport is isolated in `agent._post_messages`, so a first live run is the one remaining check. The glyph rendering was
checked in headless Edge (software WebGL): supports, pressure and force symbols, the hover cursor and the assistant panel
render; there is no automated visual test.

### Known limits
* One model-building pass per message; the agent can inspect and fix its own work, but long design-iteration loops
  (re-meshing, comparing results) are not automatic because results are only visible after the run.
* The agent does not read result fields; after the simulation the user (or a follow-up message) inspects them in the workspace.
* Gemini 2.5 Pro always "thinks", so a request takes longer than with a non-reasoning model (tens of seconds for a full build).
* Geometry is limited to the built-in primitives and structural kinds; CAD import is manual.
* Curved or partial surfaces for loads need a box region that fully covers whole surface triangles.
