import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import * as api from "../api.js";
import { Button, Modal } from "../ui";

const AuthContext = createContext(null);

/**
 * Holds the signed-in user + subscription summary for the whole app.
 * status: "loading" (first /auth/me in flight) | "ready"
 */
export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: "loading", user: null, subscription: null });

  const apply = useCallback((me) => setState({ status: "ready", user: me?.user ?? null, subscription: me?.subscription ?? null }), []);

  const refresh = useCallback(
    () =>
      api
        .me()
        .then(apply)
        .catch(() => apply(null)),
    [apply]
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  // A 401 from any API call means the session died: drop the user, guards redirect.
  useEffect(() => {
    const onUnauthorized = () => apply(null);
    window.addEventListener("gh:unauthorized", onUnauthorized);
    return () => window.removeEventListener("gh:unauthorized", onUnauthorized);
  }, [apply]);

  const value = useMemo(
    () => ({
      ...state,
      isPro: state.subscription?.tier === "pro",
      refresh,
      login: (email, password) => api.login(email, password).then((me) => (apply(me), me)),
      register: (email, password, name) => api.register(email, password, name).then((me) => (apply(me), me)),
      logout: () => api.logout().finally(() => apply(null)),
    }),
    [state, refresh, apply]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);

/** Route guard: sends anonymous visitors to /login and brings them back after. */
export function RequireAuth({ children }) {
  const { status, user } = useAuth();
  const loc = useLocation();
  if (status === "loading") return <div className="gh-boot" aria-busy="true" />;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return children;
}

/** Keeps signed-in users out of /login and /register. */
export function GuestOnly({ children }) {
  const { status, user } = useAuth();
  if (status === "loading") return <div className="gh-boot" aria-busy="true" />;
  if (user) return <Navigate to="/app" replace />;
  return children;
}

/** Only same-site relative paths are valid post-login destinations (no open redirect). */
export function safeNext(next, fallback = "/app") {
  return typeof next === "string" && next.startsWith("/") && !next.startsWith("//") ? next : fallback;
}

/** Global dialog shown whenever the API answers 402 (a Free-plan limit). */
export function UpgradeListener() {
  const [message, setMessage] = useState(null);
  useEffect(() => {
    const on = (e) => setMessage(e.detail || "This feature is part of the Pro plan.");
    window.addEventListener("gh:upgrade-required", on);
    return () => window.removeEventListener("gh:upgrade-required", on);
  }, []);
  return (
    <Modal
      open={!!message}
      onClose={() => setMessage(null)}
      title="Unlock Pro"
      actions={
        <>
          <Button variant="ghost" onClick={() => setMessage(null)}>
            Not now
          </Button>
          <Button to="/pricing" onClick={() => setMessage(null)}>
            See plans
          </Button>
        </>
      }
    >
      <p>{message}</p>
    </Modal>
  );
}
