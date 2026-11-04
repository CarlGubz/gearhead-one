# Guide agent (learning walkthrough)

A built-in agent that teaches a new user how to use the workspace. Open it with **? Guide** in the workspace toolbar,
next to **✦ AI Assistant**. The two are independent: the AI Assistant *builds* a model for you (needs an LLM key);
the Guide *teaches* you to build it yourself (needs nothing).

## What the project does (analysis)
Gearhead FEA is a React/Vite + Three.js frontend over a FastAPI backend with its own NumPy/SciPy solver. The workspace
(`frontend/src/pages/Workspace.jsx`) is a PrePoMax-style tree: left sidebar = Project, Geometry & Mesh, Beams/trusses/shells/springs,
Parts, Materials, Selection & sets; centre = 3-D viewport with selection tools; right sidebar = Analysis, Supports & loads,
Constraints, Solve & results. Model edits go through `mutate()` (server call, then replace the project and drop stale results);
Solve starts a job that is polled until complete. The normal workflow is therefore:
project → mesh → (parts, material, analysis) → select → supports → loads → (constraints) → solve → results.

## What was added
| File | Change |
|---|---|
| `frontend/src/components/guideContent.js` | **New.** Workflow steps (text, tips, completion test, prerequisite), `guideState()` (derives progress from the project and job), `nextStepIndex()`, `blocker()`, `advice()`, the FAQ topics and `matchTopic()` keyword matching |
| `frontend/src/components/GuidePanel.jsx` | **New.** The drawer: menu with progress bar and step list, tour mode (Back / Show me / Next), answer mode, FAQ chips and a question box |
| `frontend/src/components/TreeSection.jsx` | New optional `tour` prop, rendered as `data-tour` so a step can find its section |
| `frontend/src/pages/Workspace.jsx` | `? Guide` toolbar button after `✦ AI Assistant`, `guideOpen` state, renders `GuidePanel`, `tour` ids on every section |
| `frontend/src/styles.css` | Guide drawer styles (docked bottom-right so it does not cover the AI drawer) and the pulsing `.tour-highlight` outline |
| `README.md`, this file | Documentation |

No backend change and no new dependency.

## How it behaves
- **Progress is automatic.** A step is ticked when the project shows it is done (mesh exists, a support exists, a load exists, a job completed…). Optional steps (parts, material, analysis, constraints) never block progress.
- **Next step.** The first required step not yet done is marked and offered by *Go to my next step*; the top message adapts to the situation (running, failed, no mesh, no support, ready to solve…).
- **Show me.** Highlights the matching panel and opens it if collapsed (it clicks the section header when the body is not rendered). Closing the guide removes the highlight. If the panel does not exist yet (for example before a mesh), the step shows what is missing instead.
- **Questions.** Chips cover the common ones; the text box matches keywords to a topic. Unknown questions get a pointer to the tour and to the AI Assistant.
- The last viewed step is kept in `sessionStorage`; nothing is sent to the server.

## Extending
Add an object to `STEPS` (give it a `tour` id and put the same `tour="…"` on the `TreeSection`) or to `TOPICS` (`keys` are the words that select it) in `guideContent.js`. If a step is inserted, keep `done()` accurate: it drives the progress bar.

## Verification and limits
- `npx vite build` in `frontend/` succeeds.
- Not covered by automated tests (the repository has no frontend test runner); check manually: open **? Guide**, press *Start the tour*, press *Show me* on several steps, add a support and a load and watch the ticks, and try questions such as "fixed" or "solve failed".
- The guide is rule-based, not an LLM: it only answers the topics in `TOPICS`. Instructions mirror the UI as of this change and need updating if panels are renamed.
