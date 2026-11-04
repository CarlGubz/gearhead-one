import React, { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Renders a workspace (CFD, CAD) inside its own shadow root.
 *
 * The CFD and CAD workbenches came from stand-alone apps whose stylesheets style bare elements and use class names
 * that the FEA workspace uses too (.viewport, .body, .toolbar, .legend …). A shadow root keeps the three style sheets
 * apart while the workbenches stay part of the same React tree (router, auth, project state). Their CSS is injected
 * as text, with document-level selectors mapped onto the shadow host:
 *   :root[data-theme="x"] → :host([data-theme="x"])   :root → :host   html, body, #root / body → :host
 * The page's theme attribute is mirrored onto the host so the light ("vellum") / dark ("blueprint") tokens follow it.
 * Fonts (@font-face) and the --gh-* tokens of the page are inherited into the shadow root.
 */
export function hostCss(css) {
  return css
    .replace(/:root\[data-theme="?(\w+)"?\]/g, ':host([data-theme="$1"])')
    .replace(/:root/g, ":host")
    .replace(/html,\s*body,\s*#root/g, ":host")
    .replace(/(^|[}\s])body(\s*\{)/g, "$1:host$2");
}

export default function ShadowPane({ css, className, hidden, onHost, children, ...rest }) {
  const hostRef = useRef(null);
  const [mount, setMount] = useState(null);

  useLayoutEffect(() => {
    const host = hostRef.current;
    const root = host.shadowRoot || host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = hostCss(css || "");
    const box = document.createElement("div");
    box.className = "shadow-mount";
    box.style.cssText = "height:100%;width:100%;display:flex;flex-direction:column;min-height:0;";
    root.replaceChildren(style, box);
    const sync = () => host.setAttribute("data-theme", document.documentElement.getAttribute("data-theme") || "light");
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    setMount(box);
    onHost?.(host);
    return () => { obs.disconnect(); root.replaceChildren(); setMount(null); };
  }, [css]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={hostRef} className={className} style={{ display: hidden ? "none" : undefined }} {...rest}>
      {mount && createPortal(children, mount)}
    </div>
  );
}
