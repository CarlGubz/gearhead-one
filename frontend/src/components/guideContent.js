/**
 * Content and state logic for the Guide agent (the "Guide" button in the workspace toolbar).
 * The guide is rule-based: it reads the live project state, tells the user which step is next and
 * answers common questions from the topic list below. It needs no API key and no network.
 */

/** Workflow steps in the order a user normally works through them. `tour` is the data-tour id to highlight. */
export const STEPS = [
  {
    id: "project", title: "Create or open a project", tour: "project",
    text: "Everything lives in a project. Click Project in the command ribbon at the top (the icon or its ▾ arrow) to open its dialog, type a name and press New, or pick an existing project from the list. "
      + "You can also open a saved .gfea.json file, save your work, and export a CalculiX/Abaqus-style .inp deck. "
      + "A project can hold several models: the Model tab of the left panel stacks them like layers (+ adds one, ⧉ duplicates the active one).",
    tip: "Ctrl+Z / Ctrl+Y undo and redo every model edit.",
    done: (s) => s.hasProject,
  },
  {
    id: "mesh", title: "Build the geometry and mesh", tour: "geometry",
    text: "Open Geometry in the ribbon. Choose a shape (box, cylinder, sphere, tube, plate with hole, L-bracket, cone, torus), enter its dimensions, "
      + "pick an element type (Tet4/Tet10 tetrahedra or Hex8/Hex20 bricks) and press Generate. You can also import a mesh (.inp, Gmsh .msh) or a CAD file (STEP, IGES, STL). "
      + "For beams, trusses, shells and springs use the Frames command instead. The Geometry tab of the left panel then shows the input geometry and the mesh.",
    tip: "Bending is stiffer than reality with Tet4/Hex8. Use the quadratic types (Tet10 / Hex20) for accurate stresses.",
    done: (s) => s.hasMesh,
    needs: "hasMesh",
  },
  {
    id: "parts", title: "Check the parts (optional)", tour: "parts",
    text: "The Parts command lists each body in the model. You can rename, hide, move or delete a part and give it its own material. "
      + "To combine several bodies, use Add as separate body in Geometry; touching parts are tied together automatically.",
    done: (s) => s.hasMesh,
    optional: true, needs: "hasMesh",
  },
  {
    id: "material", title: "Choose the material", tour: "materials",
    text: "Open Materials in the ribbon and set Young's modulus, Poisson's ratio and density, or pick one from the library (steel, aluminium and more). "
      + "Thermal and plastic properties are needed only for heat-transfer or nonlinear analyses. Assign materials per part with the Parts command.",
    tip: "Keep units consistent: the default set is SI-like (m, N, Pa, kg).",
    done: (s) => s.hasMesh,
    optional: true, needs: "project",
  },
  {
    id: "analysis", title: "Pick the analysis type", tour: "analysis",
    text: "In the Analysis tab of the left panel choose what you want to learn: static (stress and deflection), nonlinear, frequency, buckling, dynamic, "
      + "heat transfer (steady or transient) or thermo-mechanical. Each type shows its own settings, such as number of modes or time steps.",
    tip: "The Free plan includes static analysis; Pro unlocks all the others.",
    done: (s) => s.hasMesh,
    optional: true, needs: "project",
  },
  {
    id: "select", title: "Select nodes or faces", tour: "selection",
    text: "Supports and loads attach to a selection. In the toolbar at the top of the 3-D view pick the node tool or the face tool, then click the model "
      + "(Shift adds or removes), or drag a box over a region (→ inside only, ← touching; Shift adds, Ctrl removes). The Selection command also selects by coordinate range, face angle or id, and lets you save named sets.",
    tip: "To fix or load a whole face, use the face tool (or By coordinate). The node tool picks a single node.",
    done: (s) => s.hasSupports || s.hasLoads,
    needs: "hasMesh",
  },
  {
    id: "supports", title: "Add supports (boundary conditions)", tour: "loads",
    text: "In Supports & loads (Analysis tab), with a selection active, add a support: fixed, symmetry or a prescribed displacement. "
      + "The model must not be able to move as a rigid body, so fix a whole face rather than a single node. Supports appear as blue cones in the view.",
    done: (s) => s.hasSupports,
    needs: "hasMesh",
  },
  {
    id: "loads", title: "Add loads", tour: "loads",
    text: "Select the face or nodes to load and add a force, pressure, traction, gravity or, for thermal analyses, a temperature, heat source, flux or convection. "
      + "Loads appear as red 3-D arrows pointing the way they act.",
    done: (s) => s.hasLoads,
    needs: "hasMesh",
  },
  {
    id: "constraints", title: "Add constraints (optional)", tour: "constraints",
    text: "Constraints couple parts of the model: reference points, rigid bodies, ties and frictionless contact between parts. "
      + "Most simple models do not need any.",
    done: (s) => s.hasConstraints,
    optional: true, needs: "hasMesh",
  },
  {
    id: "solve", title: "Run the simulation", tour: "solve",
    text: "Press Solve in the ribbon, or Run analysis in Solve & display (Analysis tab). A progress bar appears in the status bar at the bottom. If it fails, open the Messages panel; "
      + "the most common cause is a model that is not fully constrained.",
    done: (s) => s.jobDone,
    needs: "ready",
  },
  {
    id: "results", title: "Read the results", tour: "solve",
    text: "Pick a result field (displacement, von Mises stress, strain, temperature and more), scale the deformation, show the undeformed ghost, "
      + "set a legend range, use a section plane or probe a node. Frequency and buckling analyses add a mode browser and dynamic analyses a time-frame player. "
      + "The Results tree in the Analysis tab lists every run of the active model and its field outputs: click one to contour it. "
      + "Export CSV, VTK or a PNG screenshot from Solve & display or the Results tree.",
    done: (s) => s.jobDone,
    needs: "jobDone",
  },
];

/** Derive everything the guide needs from the live workspace state. */
export function guideState({ project, job, solving, isPro }) {
  const hasProject = !!project;
  const hasMesh = !!project?.elements?.length;
  const hasSupports = (project?.boundary_conditions?.length || 0) > 0;
  const hasLoads = (project?.loads?.length || 0) > 0;
  const hasConstraints = (project?.constraints?.length || 0) + (project?.reference_points?.length || 0) > 0;
  const jobDone = job?.status === "completed";
  return {
    hasProject, hasMesh, hasSupports, hasLoads, hasConstraints, jobDone, solving: !!solving, isPro: !!isPro,
    jobFailed: job?.status === "failed",
    analysisType: project?.analysis_type || "static",
    ready: hasMesh && hasSupports && hasLoads,
  };
}

/** The first non-optional step that is not finished yet (index into STEPS). */
export function nextStepIndex(state) {
  const i = STEPS.findIndex((st) => !st.optional && !st.done(state));
  return i === -1 ? STEPS.length - 1 : i;
}

/** A step's prerequisite, as a human sentence, or null when it can be done now. */
export function blocker(step, s) {
  if (step.needs === "project" && !s.hasProject) return "Create or select a project first.";
  if (step.needs === "hasMesh" && !s.hasMesh) return s.hasProject ? "Generate or import a mesh first." : "Create a project and a mesh first.";
  if (step.needs === "ready" && !s.ready) {
    if (!s.hasMesh) return "Generate or import a mesh first.";
    if (!s.hasSupports) return "Add at least one support first.";
    return "Add at least one load first.";
  }
  if (step.needs === "jobDone" && !s.jobDone) return "Run the simulation first.";
  return null;
}

/** One short line saying what to do right now (shown at the top of the guide). */
export function advice(s) {
  if (s.solving) return "The simulation is running. Watch the progress bar at the bottom.";
  if (s.jobFailed) return "The last run failed. Open the Messages panel; the usual cause is a model that is not fully constrained (fix a whole face).";
  if (!s.hasProject) return "Start by creating a project in the Project section.";
  if (!s.hasMesh) return "Next: generate or import a mesh in Geometry & Mesh.";
  if (!s.hasSupports) return "Next: select a face and add a support so the part cannot move freely.";
  if (!s.hasLoads) return "Next: select a face and add a load.";
  if (!s.jobDone) return "Your model is ready. Press Solve in Solve & results.";
  return "Results are ready. Explore the fields, deformation and probe tools, or export them.";
}

/** Questions the guide can answer. `keys` are matched against the user's words. */
export const TOPICS = [
  {
    id: "start", label: "Where do I start?", keys: ["start", "begin", "first", "new", "how to use", "getting started"],
    answer: "Follow the workflow in order: 1) create a project, 2) mesh a shape, 3) set the material and analysis, 4) select a face, "
      + "5) add a support and a load, 6) press Solve, 7) read the results. Use \"Start the tour\" and I will show you each section and track your progress.",
  },
  {
    id: "select", label: "How do I select a face or node?", keys: ["select", "pick", "click", "face", "node", "highlight"],
    answer: "Use the buttons at the top of the 3-D view: the node tool snaps to the nearest node (on quadratic meshes also to mid-side nodes), "
      + "the face tool selects the face under the cursor, and the angle tool selects connected faces. Shift+click adds or removes. "
      + "Selected nodes show as magenta spheres. The Selection & sets section also selects by coordinate range or id and saves named sets.",
    tour: "selection",
  },
  {
    id: "support", label: "How do I fix the model?", keys: ["support", "fix", "fixed", "clamp", "boundary", "constrain", "constrained", "rigid body", "not fully", "symmetry"],
    answer: "Select a whole face, then add a Fixed support in Supports & loads. If you see \"model is not fully constrained\", the part can still move or rotate freely: "
      + "restrain a full face, or add enough supports to stop all six rigid-body motions.",
    tour: "loads",
  },
  {
    id: "load", label: "How do I apply a load?", keys: ["load", "force", "pressure", "gravity", "weight", "traction", "push", "pull"],
    answer: "Select the nodes or face, choose the load type in Supports & loads (force, pressure, traction, gravity, remote force/moment, centrifugal, thermal strain), "
      + "enter the value and press Add. Loads show as red arrows. A pressure pushes into the face; a negative value pulls out.",
    tour: "loads",
  },
  {
    id: "mesh", label: "Which element type should I use?", keys: ["mesh", "element", "tet", "hex", "brick", "gmsh", "density", "refine", "accuracy", "stiff"],
    answer: "Tet4 and Hex8 are fast but intentionally simple and bending looks too stiff. Use Tet10 or Hex20 for accurate stresses. "
      + "Bricks (Hex) are structured and give the cleanest results on regular shapes; tetrahedra work on anything. "
      + "Choose Gmsh in the mesher options for size fields, curvature refinement and CAD import.",
    tour: "geometry",
  },
  {
    id: "cad", label: "Can I import my own geometry?", keys: ["import", "cad", "step", "iges", "stl", "msh", "inp", "upload", "file"],
    answer: "Yes. In Geometry & Mesh use import for mesh files (.inp, Gmsh .msh) or CAD (STEP, IGES, BREP, STL), which is meshed with Gmsh into tetrahedra or bricks. "
      + "Large CAD files can take up to a minute. Saved projects (.gfea.json) open from the Project section.",
    tour: "geometry",
  },
  {
    id: "analysis", label: "What analysis types exist?", keys: ["analysis", "static", "modal", "frequency", "buckling", "dynamic", "thermal", "heat", "nonlinear", "plastic", "nlgeom", "pro", "plan"],
    answer: "Static (stress and deflection), nonlinear (plasticity), frequency (natural modes), linear buckling, dynamic (time response), "
      + "heat transfer steady and transient, and thermo-mechanical, plus large deformation (NLGEOM). The Free plan includes static; Pro unlocks the rest. "
      + "Buckling needs a reference load that compresses the part.",
    tour: "analysis",
  },
  {
    id: "solve", label: "My solve failed. Why?", keys: ["fail", "error", "converge", "slow", "solve", "run", "simulation", "problem", "wrong", "doesn't work", "not working"],
    answer: "Check the Messages panel at the bottom first. Typical causes: the model is not fully constrained (fix a whole face); a buckling load that does not compress the part; "
      + "a nonlinear run that did not converge (reduce the load or add increments); a Pro-only analysis on the Free plan. Large meshes solve slowly; use a coarser mesh with quadratic elements.",
    tour: "solve",
  },
  {
    id: "results", label: "How do I read the results?", keys: ["result", "stress", "von mises", "displacement", "contour", "legend", "probe", "deform", "animate", "export", "csv", "vtk", "png"],
    answer: "After a run, pick a field in Solve & results (displacement, von Mises, Tresca, principal, strain, temperature, heat flux). "
      + "Adjust the deformation scale, show the undeformed ghost and mesh edges, set the legend range, add a section plane, or turn on the probe and click a node. "
      + "Export CSV, VTK or a PNG when you are done.",
    tour: "solve",
  },
  {
    id: "parts", label: "How do I use several parts?", keys: ["part", "parts", "assembly", "contact", "tie", "combine", "multiple", "append", "material per"],
    answer: "Add another body with Append in Geometry & Mesh, then manage it in Parts: rename, hide, move, delete and assign a material. "
      + "Touching parts are tied automatically; Constraints adds frictionless contact, rigid bodies and reference points.",
    tour: "parts",
  },
  {
    id: "undo", label: "Can I undo a mistake?", keys: ["undo", "redo", "mistake", "revert", "ctrl"],
    answer: "Yes. Ctrl+Z undoes and Ctrl+Y (or Ctrl+Shift+Z) redoes any model edit, and the Project section shows the undo and redo buttons with the name of the step. "
      + "This also undoes steps taken by the AI Assistant.",
    tour: "project",
  },
  {
    id: "ai", label: "What does the AI Assistant do?", keys: ["ai", "assistant", "gemini", "chat", "automatic", "automate", "describe"],
    answer: "The AI Assistant builds the whole model from a plain-language description (geometry, material, supports, loads) and starts the simulation for you. "
      + "I am the Guide: I teach you to do it yourself and follow your progress. Use whichever suits you; they work side by side.",
    tour: "ai",
  },
  {
    id: "save", label: "How do I save my work?", keys: ["save", "open", "load project", "backup", "persist"],
    answer: "Projects are stored on the server automatically. Use Save in the Project section to download a .gfea.json file, Open to load one back as a new project, "
      + "and Export .inp for a CalculiX/Abaqus-style deck.",
    tour: "project",
  },
];

/** Best topic for a free-text question, or null. */
export function matchTopic(question) {
  const q = question.toLowerCase();
  let best = null, bestScore = 0;
  for (const t of TOPICS) {
    let score = 0;
    for (const k of t.keys) {
      if (k.includes(" ") ? q.includes(k) : new RegExp(`\\b${k.replace(/[^\w']/g, "")}`).test(q)) score += k.length > 3 ? 2 : 1;
    }
    if (score > bestScore) { best = t; bestScore = score; }
  }
  return best;
}
