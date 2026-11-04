import React, { useMemo, useState } from "react";
import { Num } from "./fields.jsx";
import { boundingBox, fmt } from "./utils.js";

/**
 * CAM workspace, Setup command: the raw stock block around the active model (its bounding box plus a machining
 * allowance per side) and the volume that would have to be removed. Toolpath generation is not part of the app yet.
 */
export default function CamPanel({ project, quality }) {
  const [allowance, setAllowance] = useState(2);       // mm per side
  const box = useMemo(() => (project?.nodes?.length ? boundingBox(project.nodes) : null), [project?.nodes]);
  if (!box) return <p className="hint">The active model has no geometry. Create or import one in the CAD workspace first.</p>;
  const a = allowance / 1000;
  const stock = box.size.map((v) => v + 2 * a);
  const stockVol = stock[0] * stock[1] * stock[2];
  const partVol = quality?.total_volume;
  const mm = (v) => fmt(v * 1000, 4);
  return (
    <>
      <h4 style={{ marginTop: 0 }}>Stock (box around the model)</h4>
      <Num label="Allowance per side" unit="mm" value={allowance} min={0} onCommit={setAllowance} />
      <table className="kv">
        <tbody>
          <tr><td>Model extents</td><td>{box.size.map(mm).join(" × ")} mm</td></tr>
          <tr><td>Stock size</td><td>{stock.map(mm).join(" × ")} mm</td></tr>
          <tr><td>Stock origin (min corner)</td><td>({box.lo.map((v) => mm(v - a)).join(", ")}) mm</td></tr>
          <tr><td>Stock volume</td><td>{fmt(stockVol * 1e6, 4)} cm³</td></tr>
          {partVol !== undefined && <tr><td>Part volume</td><td>{fmt(partVol * 1e6, 4)} cm³</td></tr>}
          {partVol !== undefined && <tr><td>Material to remove</td><td>{fmt((stockVol - partVol) * 1e6, 4)} cm³ ({fmt((1 - partVol / stockVol) * 100, 3)} %)</td></tr>}
        </tbody>
      </table>
      <p className="hint">
        Toolpaths, machining simulation and post-processing are planned for the CAM workspace and are not available yet.
        The model stays the same file, so you can switch back to CAE to check the part under load.
      </p>
    </>
  );
}
