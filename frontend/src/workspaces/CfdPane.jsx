import React from "react";
import cfdCss from "../cfd/styles.css?inline";
import CfdWorkspace from "../cfd/App.tsx";
import { setCssRoot } from "../cfd/lib/theme.ts";
import ShadowPane from "../components/ShadowPane.jsx";

/** CFD workspace body (formerly CFDFlow) in its own shadow root; loaded on first use (own bundle chunk). */
export default function CfdPane({ projectId, projectName, onCase }) {
  return (
    <ShadowPane css={cfdCss} className="ws-pane" data-dialog-anchor="" onHost={setCssRoot}>
      <CfdWorkspace projectId={projectId} projectName={projectName} onCase={onCase} />
    </ShadowPane>
  );
}
