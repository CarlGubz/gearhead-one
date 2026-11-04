import { Toast } from "./components/ui";
import { AppProvider, Tab, useApp } from "./store";
import AssemblyWorkspace from "./workspaces/AssemblyWorkspace";
import CamWorkspace from "./workspaces/CamWorkspace";
import DrawingWorkspace from "./workspaces/DrawingWorkspace";
import ModelWorkspace from "./workspaces/ModelWorkspace";
import SimulateWorkspace from "./workspaces/SimulateWorkspace";
import SketchWorkspace from "./workspaces/SketchWorkspace";

export { CAD_TABS } from "./tabs";

function Body() {
  const { project, tab, toast, clearToast, busy } = useApp();
  return (
    <div className="app" style={{ gridTemplateRows: "1fr 24px" }}>
      {project ? (
        <>
          {tab === "sketch" && <SketchWorkspace />}
          {tab === "model" && <ModelWorkspace />}
          {tab === "assembly" && <AssemblyWorkspace />}
          {tab === "drawing" && <DrawingWorkspace />}
          {tab === "cam" && <CamWorkspace />}
          {tab === "simulate" && <SimulateWorkspace />}
        </>
      ) : (
        <div className="empty"><div><h3>Loading the CAD project…</h3></div></div>
      )}
      <div className="statusbar">
        <span>{project ? `CAD · ${project.name}` : "no project"}</span>
        <span className="grow" />
        {busy ? <span className="ok">⏳ {busy}</span> : <span>ready</span>}
        <span>mm · Z-up</span>
      </div>
      {toast && <Toast msg={toast.msg} kind={toast.kind} onClose={clearToast} />}
    </div>
  );
}

/**
 * The CAD / CAM workspace of the Gearhead app (formerly the CADWeb front end), rendered inside a shadow root by the
 * Workspace page. `cadProjectId` is the CAD project of the open Gearhead project; `tab` comes from the ribbon.
 * Bump `refreshKey` to reload everything (after the demo was added or a Transfer created parts).
 */
export default function CadWorkspace({ cadProjectId, tab, onTab, refreshKey = 0 }: {
  cadProjectId: number; tab: Tab; onTab: (t: Tab) => void; refreshKey?: number;
}) {
  return (
    <AppProvider key={`${cadProjectId}:${refreshKey}`} cadProjectId={cadProjectId} tab={tab} onTab={onTab}>
      <Body />
    </AppProvider>
  );
}
