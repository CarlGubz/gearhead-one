import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../api.js";
import { safeNext, useAuth } from "../auth/AuthContext.jsx";
import { usePageTitle } from "../hooks/hooks.js";
import { AuthLayout } from "../layout/Layout.jsx";
import MeshHero from "../site/MeshHero.jsx";
import { Alert, Button, TextField } from "../ui";

/** The drawing shown beside every auth form. */
function AuthArt() {
  return (
    <div className="gh-auth-art">
      <p className="gh-auth-art__quote">
        Every great part started as a <em>bad</em> first sheet.
      </p>
      <MeshHero caption={false} legend={false} />
    </div>
  );
}

/** Tiny controlled-form helper: values, per-field setters, submit + error state. */
function useForm(initial, submit) {
  const [values, setValues] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const bind = (name) => ({
    value: values[name],
    onChange: (e) => setValues((v) => ({ ...v, [name]: e.target.value })),
  });
  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await submit(values);
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return { values, bind, onSubmit, busy, error, setError };
}

export function Login() {
  usePageTitle("Sign in");
  const { login } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const form = useForm({ email: "", password: "" }, async (v) => {
    await login(v.email.trim(), v.password);
    nav(safeNext(params.get("next")), { replace: true });
  });

  return (
    <AuthLayout title="Welcome back." subtitle="Sign in to open your workspace." aside={<AuthArt />}>
      <form className="gh-auth-form" onSubmit={form.onSubmit} noValidate>
        {params.get("reset") && <Alert tone="success">Password updated. Sign in with your new password.</Alert>}
        {form.error && <Alert tone="danger">{form.error}</Alert>}
        <TextField label="Email" type="email" autoComplete="email" required autoFocus {...form.bind("email")} />
        <TextField label="Password" type="password" autoComplete="current-password" required {...form.bind("password")} />
        <Button type="submit" block loading={form.busy}>Sign in</Button>
        <p className="gh-auth-alt"><Link to="/forgot-password">Forgot your password?</Link></p>
        <p className="gh-auth-alt">
          New here? <Link to={`/register${params.get("next") ? `?next=${encodeURIComponent(params.get("next"))}` : ""}`}>Create a free account</Link>
        </p>
      </form>
    </AuthLayout>
  );
}

export function Register() {
  usePageTitle("Create account");
  const { register } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const plan = params.get("plan");
  const form = useForm({ name: "", email: "", password: "" }, async (v) => {
    await register(v.email.trim(), v.password, v.name);
    // Coming from "Get Pro": continue straight to the pricing page's checkout.
    nav(plan ? "/pricing" : safeNext(params.get("next")), { replace: true });
  });

  return (
    <AuthLayout
      title="Start your first sheet."
      subtitle={plan ? "Create your account, then pick Pro to pay with GCash." : "Free to begin. No card required."}
      aside={<AuthArt />}
    >
      <form className="gh-auth-form" onSubmit={form.onSubmit} noValidate>
        {form.error && <Alert tone="danger">{form.error}</Alert>}
        <TextField label="Name" autoComplete="name" {...form.bind("name")} />
        <TextField label="Email" type="email" autoComplete="email" required {...form.bind("email")} />
        <TextField
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          hint="At least 10 characters. A short phrase works well."
          {...form.bind("password")}
        />
        <Button type="submit" block loading={form.busy}>Create account</Button>
        <p className="gh-auth-alt">Already have an account? <Link to="/login">Sign in</Link></p>
      </form>
    </AuthLayout>
  );
}

export function ForgotPassword() {
  usePageTitle("Reset password");
  const [sent, setSent] = useState(false);
  const form = useForm({ email: "" }, async (v) => {
    await api.forgotPassword(v.email.trim());
    setSent(true);
  });
  return (
    <AuthLayout title="Reset your password." subtitle="We'll email you a link that works for one hour." aside={<AuthArt />}>
      {sent ? (
        <div className="gh-auth-form">
          <Alert tone="success">
            If an account exists for that address, a reset link is on its way. Check your inbox (and spam).
          </Alert>
          <p className="gh-auth-alt"><Link to="/login">Back to sign in</Link></p>
        </div>
      ) : (
        <form className="gh-auth-form" onSubmit={form.onSubmit} noValidate>
          {form.error && <Alert tone="danger">{form.error}</Alert>}
          <TextField label="Email" type="email" autoComplete="email" required autoFocus {...form.bind("email")} />
          <Button type="submit" block loading={form.busy}>Send reset link</Button>
          <p className="gh-auth-alt"><Link to="/login">Back to sign in</Link></p>
        </form>
      )}
    </AuthLayout>
  );
}

export function ResetPassword() {
  usePageTitle("Choose a new password");
  const nav = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const form = useForm({ password: "" }, async (v) => {
    await api.resetPassword(token, v.password);
    nav("/login?reset=1", { replace: true });
  });
  return (
    <AuthLayout title="Choose a new password." aside={<AuthArt />}>
      {!token ? (
        <Alert tone="danger">This reset link is incomplete. <Link to="/forgot-password">Request a new one</Link>.</Alert>
      ) : (
        <form className="gh-auth-form" onSubmit={form.onSubmit} noValidate>
          {form.error && <Alert tone="danger">{form.error}</Alert>}
          <TextField
            label="New password"
            type="password"
            autoComplete="new-password"
            required
            autoFocus
            hint="At least 10 characters."
            {...form.bind("password")}
          />
          <Button type="submit" block loading={form.busy}>Update password</Button>
        </form>
      )}
    </AuthLayout>
  );
}
