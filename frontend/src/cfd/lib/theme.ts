import { useEffect, useState } from "react";

const KEY = "cfdflow-theme";

export function initTheme() {
  const saved = localStorage.getItem(KEY);
  const theme = saved ?? (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = theme;
}

export function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem(KEY, next);
  window.dispatchEvent(new Event("cfdflow-theme"));
}

/** Re-render whenever the theme flips (canvases read CSS variables when drawing). */
export function useThemeTick(): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const h = () => setN((x) => x + 1);
    window.addEventListener("cfdflow-theme", h);
    return () => window.removeEventListener("cfdflow-theme", h);
  }, []);
  return n;
}

/**
 * Element whose computed style holds the CFD tokens (--accent, --canvas-bg …). In the integrated Gearhead app the CFD
 * workbench lives in a shadow root and its tokens are defined on the shadow host, so the host registers itself here.
 */
let cssRoot: Element | null = null;
export function setCssRoot(el: Element | null) {
  cssRoot = el;
}

export function cssVar(name: string): string {
  return getComputedStyle(cssRoot ?? document.documentElement).getPropertyValue(name).trim();
}
