import { forwardRef, useEffect, useImperativeHandle, useRef, ReactNode } from "react";
import { Viewer } from "./Viewer";

export interface ViewportHandle { viewer: () => Viewer | null }

/** React host for the Three.js viewer.  `setup` runs whenever `deps` change and fills the scene. */
export const Viewport = forwardRef<ViewportHandle, {
  setup: (v: Viewer) => void;
  deps: unknown[];
  fitOn?: unknown;
  onReady?: (v: Viewer) => void;
  children?: ReactNode;
  toolbar?: boolean;
}>(function Viewport({ setup, deps, fitOn, onReady, children, toolbar = true }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<Viewer | null>(null);
  const setupRef = useRef(setup);
  setupRef.current = setup;
  const lastFit = useRef<unknown>(Symbol());

  useImperativeHandle(ref, () => ({ viewer: () => viewer.current }));

  useEffect(() => {
    const v = new Viewer(host.current!);
    viewer.current = v;
    onReady?.(v);
    return () => { v.dispose(); viewer.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    setupRef.current(v);
    if (lastFit.current !== fitOn) { lastFit.current = fitOn; v.fit(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const v = () => viewer.current;
  return (
    <>
      <div className="viewport" ref={host} />
      {toolbar && (
        <div className="overlay tr">
          <div className="toolbar">
            {(["iso", "front", "right", "top"] as const).map((n) => (
              <button key={n} className="btn ghost sm" title={`${n} view`} onClick={() => v()?.setView(n)}>{n}</button>
            ))}
            <button className="btn ghost sm" title="Fit all" onClick={() => v()?.fit()}>⤢ fit</button>
            <button className="btn ghost sm" title="Toggle wireframe" onClick={(e) => {
              const el = e.currentTarget; el.classList.toggle("active"); v()?.setWireframe(el.classList.contains("active"));
            }}>wire</button>
          </div>
        </div>
      )}
      {children}
    </>
  );
});
