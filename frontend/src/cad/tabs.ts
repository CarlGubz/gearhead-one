import type { Tab } from "./store";

/** The six CAD workspaces; the Gearhead ribbon shows them as commands (the stand-alone app had them as top-bar tabs). */
export const CAD_TABS: { id: Tab; label: string; icon: string; hint: string }[] = [
  { id: "sketch", label: "Sketch", icon: "sketch2d", hint: "2D drafting: lines, arcs, splines, constraints, trim / extend / fillet, layers, DXF" },
  { id: "model", label: "Model", icon: "model3d", hint: "Parametric 3D: extrude, revolve, sweep, loft, holes, fillets, patterns, STEP import" },
  { id: "assembly", label: "Assembly", icon: "assembly", hint: "Components, mates (solved together), interference, bill of materials" },
  { id: "drawing", label: "Drawing", icon: "drawing", hint: "Drawing sheets: HLR views, associative dimensions, title block, PDF / SVG" },
  { id: "cam", label: "CAM", icon: "cam", hint: "Toolpaths, rest machining, collision check, removal simulation, G-code posts" },
  { id: "simulate", label: "Simulate", icon: "simulate", hint: "FEA of a part or assembly: native solver and CalculiX (nonlinear, buckling, contact)" },
];
