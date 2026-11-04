import React from "react";
import cadCss from "../cad/styles.css?inline";
import CadWorkspace from "../cad/App.tsx";
import ShadowPane from "../components/ShadowPane.jsx";

/** CAD / CAM workspace body (formerly CADWeb) in its own shadow root; loaded on first use (own bundle chunk). */
export default function CadPane({ cadProjectId, tab, onTab, refreshKey }) {
  return (
    <ShadowPane css={cadCss} className="ws-pane" data-dialog-anchor="">
      <CadWorkspace cadProjectId={cadProjectId} tab={tab} onTab={onTab} refreshKey={refreshKey} />
    </ShadowPane>
  );
}
