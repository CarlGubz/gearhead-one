import React, { useMemo } from "react";
import { IconBtn, TreeNode } from "./BrowserPanel.jsx";
import { PART_COLORS } from "./Viewport3D.jsx";
import { ELEMENTS, fmt } from "./utils.js";

const css = (c) => `rgb(${c.map((v) => Math.round(v * 255)).join(",")})`;
const STRUCT_LABELS = { B31: "Beam (B31)", T3D2: "Truss (T3D2)", SPRING2: "Spring", S4: "Shell (S4)" };

/** Title and one-line description of a part's input geometry (recorded by the server when the part was created). */
function describe(src) {
  if (!src) return { title: "Geometry", meta: "source not recorded (created before input tracking)", icon: "geometry" };
  const dims = Object.entries(src.dims || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${fmt(v, 3)}`).join(" · ");
  const at = src.offset && src.offset.some((v) => v) ? ` · at (${src.offset.map((v) => fmt(v, 3)).join(", ")})` : "";
  switch (src.kind) {
    case "primitive":
      return { title: src.label || src.shape, meta: `${dims} m · ${src.mesher === "gmsh" ? "Gmsh" : "built-in"}, density ${src.divisions}${at}`, icon: "geometry" };
    case "structural":
      return { title: src.label || src.shape, meta: `${dims} m · ${src.divisions} divisions${at}`, icon: "structural" };
    case "cad":
      return { title: src.file, meta: `CAD (${src.unit}) · ${src.family === "hex" ? "bricks" : "tetrahedra"}, order ${src.order}${src.size ? `, size ${fmt(src.size, 3)} m` : ""}`, icon: "file" };
    case "mesh_file":
      return { title: src.file, meta: `${(src.format || "").toUpperCase()} mesh file (imported as is)`, icon: "file" };
    default:
      return { title: src.label || "Custom", meta: "nodes and elements given directly", icon: "mesh" };
  }
}

/**
 * Geometry tab, laid out like PrePoMax's Geometry tree: the input geometry (what each body was made from) and the mesh
 * it produced (parts, element type, counts and quality). Eye buttons show / hide parts.
 */
export default function GeometryBrowser({ project, quality, hidden, onToggle, onOpen }) {
  const parts = project?.parts || [];
  const etype = project?.element_type;

  // one input item per distinct source: a CAD file with several solids is one input that produced several parts
  const inputs = useMemo(() => {
    const map = new Map();
    parts.forEach((p, i) => {
      const key = JSON.stringify(p.source || null);
      if (!map.has(key)) map.set(key, { src: p.source, parts: [] });
      map.get(key).parts.push({ ...p, index: i });
    });
    return [...map.values()];
  }, [parts]);

  const partNodes = useMemo(() => {
    const els = project?.elements || [];
    return parts.map((p) => {
      const s = new Set();
      for (let e = p.elem_start; e < p.elem_end && e < els.length; e++) els[e].forEach((n) => s.add(n));
      return s.size;
    });
  }, [parts, project?.elements]);

  const matName = (id) => (!id || id === "default" ? project?.material?.name || "Default"
    : (project?.materials || []).find((m) => m.id === id)?.name || id);

  if (!project) return <p className="hint ptree-empty">Open or create a project first (Project in the ribbon).</p>;

  return (
    <ul className="tree ptree">
      <TreeNode label="Geometry" icon="geometry">
        <TreeNode label="Input geometry" count={inputs.length} icon="file">
          {inputs.length === 0 && (
            <li className="tnode-note">
              No geometry yet. <button className="linkish" onClick={() => onOpen("geometry")}>Create geometry…</button>{" "}
              <button className="linkish" onClick={() => onOpen("structural")}>Frame / shell…</button>
            </li>
          )}
          {inputs.map((g, k) => {
            const d = describe(g.src);
            return (
              <TreeNode key={k} icon={d.icon} label={d.title} count={g.parts.length > 1 ? g.parts.length : null}
                defaultOpen={false} title={d.meta}>
                <li className="tnode-note">{d.meta}</li>
                {g.parts.map((p) => <TreeNode key={p.id} icon="parts" label={`→ ${p.name}`} />)}
              </TreeNode>
            );
          })}
        </TreeNode>
      </TreeNode>
      {project.elements?.length > 0 && (
        <TreeNode label="Mesh" icon="mesh" meta={ELEMENTS[etype]?.label?.split(" ")[0] || STRUCT_LABELS[etype] || etype}>
          <li className="tnode-note">
            {project.nodes.length.toLocaleString()} nodes · {project.elements.length.toLocaleString()} elements
          </li>
          <TreeNode label="Parts" count={parts.length} icon="parts">
            {parts.map((p, i) => (
              <TreeNode key={p.id} defaultOpen={false}
                label={<><span className="swatch" style={{ background: css(PART_COLORS[i % PART_COLORS.length]) }} /> {p.name}</>}
                meta={`${(p.elem_end - p.elem_start).toLocaleString()} el`}
                right={<IconBtn icon={hidden.has(p.id) ? "eyeOff" : "eye"} title={hidden.has(p.id) ? "Show part" : "Hide part"}
                  onClick={() => onToggle(p.id)} />}>
                <li className="tnode-note">{partNodes[i].toLocaleString()} nodes · material {matName(p.material_id)}</li>
              </TreeNode>
            ))}
          </TreeNode>
          {quality && (
            <TreeNode label="Mesh statistics" icon="analysis" defaultOpen={false}>
              <li className="tnode-note">
                <table className="kv">
                  <tbody>
                    {quality.total_volume !== undefined && <tr><td>Volume</td><td>{fmt(quality.total_volume)} m³</td></tr>}
                    {quality.total_length !== undefined && <tr><td>Total length</td><td>{fmt(quality.total_length)} m</td></tr>}
                    {quality.total_area !== undefined && <tr><td>Total area</td><td>{fmt(quality.total_area)} m²</td></tr>}
                    {quality.min_jacobian_ratio !== undefined && <tr><td>Min Jacobian ratio</td><td>{fmt(quality.min_jacobian_ratio)}</td></tr>}
                    {quality.max_edge_ratio !== undefined && <tr><td>Max edge ratio</td><td>{fmt(quality.max_edge_ratio)}</td></tr>}
                    {quality.inverted_elements !== undefined && <tr><td>Inverted</td><td>{quality.inverted_elements}</td></tr>}
                  </tbody>
                </table>
              </li>
            </TreeNode>
          )}
        </TreeNode>
      )}
    </ul>
  );
}
