import { useCallback, useEffect, useState } from "react";
import { errorMessage, startCheckout } from "../api.js";

const SUFFIX = "Gearhead FEA";

/** Sets document.title for the page ("Pricing · Gearhead FEA"). */
export function usePageTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} · ${SUFFIX}` : `${SUFFIX} — finite element analysis in your browser`;
  }, [title]);
}

/** Theme shared with the workspace via <html data-theme> + localStorage("webfea-theme"). */
export function useTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.getAttribute("data-theme") || "light");
  const toggle = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("webfea-theme", next);
      } catch (e) {
        /* storage blocked: theme simply won't persist */
      }
      return next;
    });
  }, []);
  return [theme, toggle];
}

/** Formats an ISO timestamp from the API (naive UTC) as e.g. "Oct 9, 2026". */
export function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
  return d.toLocaleDateString("en-PH", { year: "numeric", month: "short", day: "numeric" });
}

export const peso = (n) =>
  new Intl.NumberFormat("en-PH", { style: "currency", currency: "PHP", maximumFractionDigits: 0 }).format(n);

/** Starts a GCash checkout and sends the browser to the provider's page. */
export function useCheckout() {
  const [busyPlan, setBusyPlan] = useState(null);
  const [error, setError] = useState(null);
  const start = useCallback(async (planId) => {
    setError(null);
    setBusyPlan(planId);
    try {
      const res = await startCheckout(planId);
      window.location.assign(res.checkout_url);
    } catch (err) {
      setError(errorMessage(err));
      setBusyPlan(null);
    }
  }, []);
  return { start, busyPlan, error };
}
