import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import * as api from "../api.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { formatDate, peso, usePageTitle, useCheckout } from "../hooks/hooks.js";
import { Alert, Badge, Button, Card, Container, Section, SectionHeading, Spinner, TextField } from "../ui";
import "./pages.css";

const STATUS_TONE = { paid: "ok", pending: "warn", failed: "danger" };

/* ----------------------------------------------------------- Account page */
function PlanCard() {
  const { subscription: sub, isPro } = useAuth();
  const { start, busyPlan, error } = useCheckout();
  const [plans, setPlans] = useState({});
  useEffect(() => {
    api.getPlans().then((d) => setPlans(Object.fromEntries(d.plans.map((p) => [p.id, p])))).catch(() => {});
  }, []);
  if (!sub) return null;
  const limit = sub.limits.max_projects;
  return (
    <Card className="gh-acct__plan">
      <div className="gh-acct__row">
        <h2 className="gh-acct__h">Subscription</h2>
        <Badge tone={isPro ? "accent" : "default"}>{isPro ? "Pro" : "Free"}</Badge>
      </div>
      {isPro ? (
        <p>
          <strong>{sub.plan_name}</strong> is active until <strong>{formatDate(sub.current_period_end)}</strong>{" "}
          <span className="gh-muted">({sub.days_left} day{sub.days_left === 1 ? "" : "s"} left)</span>.
        </p>
      ) : sub.current_period_end ? (
        <p>Your Pro access ended on {formatDate(sub.current_period_end)}. Renew to unlock all analysis types again.</p>
      ) : (
        <p>You're on the Free plan. Upgrade for unlimited projects and all eight analysis types.</p>
      )}
      <p className="gh-muted">
        Projects: {sub.projects_used}
        {limit != null ? ` of ${limit}` : " (unlimited)"} · Analyses: {sub.limits.analysis_types.join(", ")}
      </p>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="gh-acct__actions">
        <Button loading={busyPlan === "pro_monthly"} disabled={!!busyPlan} onClick={() => start("pro_monthly")}>
          {isPro ? "Add 30 days" : "Pro · 30 days"}{plans.pro_monthly ? ` — ${peso(plans.pro_monthly.amount)}` : ""}
        </Button>
        <Button
          variant="secondary"
          loading={busyPlan === "pro_annual"}
          disabled={!!busyPlan}
          onClick={() => start("pro_annual")}
        >
          {isPro ? "Add 365 days" : "Pro · 365 days"}{plans.pro_annual ? ` — ${peso(plans.pro_annual.amount)}` : ""}
        </Button>
      </div>
      <p className="gh-muted gh-acct__fine">Paid with GCash. Pro never renews automatically; new time is added after your current end date.</p>
    </Card>
  );
}

function PaymentHistory() {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    api.getPayments().then(setRows).catch(() => setRows([]));
  }, []);
  return (
    <Card flat>
      <h2 className="gh-acct__h">Payment history</h2>
      {rows === null ? (
        <p><Spinner /> Loading…</p>
      ) : rows.length === 0 ? (
        <p className="gh-muted">No payments yet.</p>
      ) : (
        <div className="gh-table-wrap">
          <table className="gh-table">
            <thead>
              <tr><th>Date</th><th>Plan</th><th>Status</th><th className="num">Amount</th></tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td>{formatDate(p.created_at)}</td>
                  <td>{p.plan_name}</td>
                  <td><Badge tone={STATUS_TONE[p.status]}>{p.status}</Badge></td>
                  <td className="num">{peso(p.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function PasswordForm() {
  const [v, setV] = useState({ current: "", next: "" });
  const [state, setState] = useState({ busy: false, error: "", done: false });
  const submit = async (e) => {
    e.preventDefault();
    setState({ busy: true, error: "", done: false });
    try {
      await api.changePassword(v.current, v.next);
      setV({ current: "", next: "" });
      setState({ busy: false, error: "", done: true });
    } catch (err) {
      setState({ busy: false, error: api.errorMessage(err), done: false });
    }
  };
  return (
    <Card flat>
      <h2 className="gh-acct__h">Password</h2>
      <form className="gh-auth-form" onSubmit={submit} noValidate>
        {state.error && <Alert tone="danger">{state.error}</Alert>}
        {state.done && <Alert tone="success">Password changed. Other devices have been signed out.</Alert>}
        <TextField label="Current password" type="password" autoComplete="current-password" value={v.current}
          onChange={(e) => setV({ ...v, current: e.target.value })} required />
        <TextField label="New password" type="password" autoComplete="new-password" hint="At least 10 characters."
          value={v.next} onChange={(e) => setV({ ...v, next: e.target.value })} required />
        <Button type="submit" variant="secondary" loading={state.busy}>Change password</Button>
      </form>
    </Card>
  );
}

export function Account() {
  usePageTitle("Account");
  const { user, logout } = useAuth();
  const nav = useNavigate();
  return (
    <Section labelledBy="acct-title">
      <SectionHeading as="h1" no="ID" eyebrow="Account & billing" id="acct-title"
        title={<>{user.full_name ? `${user.full_name.split(" ")[0]}'s` : "Your"} <em>account</em></>}
        lede={user.email} />
      <div className="gh-acct">
        <PlanCard />
        <PaymentHistory />
        <PasswordForm />
        <Card flat className="gh-acct__session">
          <h2 className="gh-acct__h">Session</h2>
          <p className="gh-muted">Signed in as {user.email}.</p>
          <div className="gh-acct__actions">
            <Button to="/app" variant="secondary">Open workspace</Button>
            <Button variant="danger" onClick={() => logout().finally(() => nav("/"))}>Sign out</Button>
          </div>
        </Card>
      </div>
    </Section>
  );
}

/* ----------------------------------------------- Return from the provider */
/**
 * Landing page after GCash checkout. The redirect itself proves nothing, so
 * we ask the server to verify the payment with the provider (polling briefly
 * because the webhook / GCash confirmation can lag a few seconds).
 */
export function BillingReturn() {
  usePageTitle("Payment");
  const [params] = useSearchParams();
  const paymentId = params.get("payment");
  const cancelled = params.get("result") === "cancel";
  const { refresh } = useAuth();
  const [state, setState] = useState({ phase: "checking", payment: null, sub: null, error: "" });
  const tries = useRef(0);

  useEffect(() => {
    if (!paymentId) {
      setState({ phase: "error", error: "Missing payment reference." });
      return undefined;
    }
    let timer;
    let alive = true;
    const check = async () => {
      try {
        const res = await api.verifyPayment(paymentId);
        if (!alive) return;
        if (res.payment.status === "paid") {
          await refresh();
          if (alive) setState({ phase: "paid", payment: res.payment, sub: res.subscription });
        } else if (res.payment.status === "failed" || cancelled) {
          setState({ phase: cancelled ? "cancelled" : "failed", payment: res.payment });
        } else if (tries.current++ < 8) {
          timer = setTimeout(check, 2500);
        } else {
          setState({ phase: "pending", payment: res.payment });
        }
      } catch (err) {
        if (alive) setState({ phase: "error", error: api.errorMessage(err) });
      }
    };
    check();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [paymentId, cancelled, refresh]);

  const { phase, payment, sub, error } = state;
  return (
    <Section labelledBy="ret-title">
      <Container className="gh-return">
        <Card accent={phase === "paid"}>
          {phase === "checking" && (
            <>
              <h1 id="ret-title" className="gh-return__h"><Spinner /> Confirming your payment…</h1>
              <p className="gh-muted">This usually takes a few seconds. Please don't close this tab.</p>
            </>
          )}
          {phase === "paid" && (
            <>
              <h1 id="ret-title" className="gh-return__h">You're on <em>Pro</em>.</h1>
              <p>
                Payment of <strong>{peso(payment.amount)}</strong> received for {payment.plan_name}. Pro is active until{" "}
                <strong>{formatDate(sub.current_period_end)}</strong>.
              </p>
              <div className="gh-acct__actions">
                <Button to="/app">Open workspace</Button>
                <Button to="/account" variant="secondary">View receipt</Button>
              </div>
            </>
          )}
          {(phase === "cancelled" || phase === "failed") && (
            <>
              <h1 id="ret-title" className="gh-return__h">
                {phase === "cancelled" ? "Payment cancelled." : "Payment didn't go through."}
              </h1>
              <p className="gh-muted">You haven't been charged and your plan is unchanged.</p>
              <div className="gh-acct__actions">
                <Button to="/pricing">Try again</Button>
                <Button to="/account" variant="secondary">Back to account</Button>
              </div>
            </>
          )}
          {phase === "pending" && (
            <>
              <h1 id="ret-title" className="gh-return__h">Still waiting on GCash.</h1>
              <p>
                We haven't received confirmation yet. If you approved the payment, Pro will switch on automatically within a few
                minutes — check <Link to="/account">your account</Link>.
              </p>
              <Button onClick={() => window.location.reload()}>Check again</Button>
            </>
          )}
          {phase === "error" && (
            <>
              <h1 id="ret-title" className="gh-return__h">We couldn't check that payment.</h1>
              <Alert tone="danger">{error}</Alert>
              <Button to="/account" variant="secondary">Go to account</Button>
            </>
          )}
        </Card>
      </Container>
    </Section>
  );
}

/* ------------------------------------------------ Sandbox GCash simulator */
/** Stand-in for the GCash approval screen. Only works when the backend runs PAYMENT_PROVIDER=mock. */
export function MockGcash() {
  usePageTitle("GCash sandbox");
  const { paymentId } = useParams();
  const nav = useNavigate();
  const [payment, setPayment] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    api.getPayment(paymentId).then(setPayment).catch((e) => setError(api.errorMessage(e)));
  }, [paymentId]);

  const finish = async (outcome) => {
    setBusy(outcome);
    try {
      await api.mockComplete(paymentId, outcome);
      nav(`/billing/return?payment=${paymentId}&result=${outcome === "paid" ? "success" : "cancel"}`, { replace: true });
    } catch (e) {
      setError(api.errorMessage(e, "The sandbox is not enabled on this server."));
      setBusy("");
    }
  };

  return (
    <div className="gh-mock">
      <div className="gh-mock__phone">
        <p className="gh-mock__banner">SANDBOX · NO REAL MONEY</p>
        <h1 className="gh-mock__title">GCash payment</h1>
        {error && <Alert tone="danger">{error}</Alert>}
        {!payment && !error && <p><Spinner /> Loading…</p>}
        {payment && (
          <>
            <p className="gh-mock__to">Pay to <strong>Gearhead FEA</strong></p>
            <p className="gh-mock__amt">{peso(payment.amount)}</p>
            <p className="gh-muted">{payment.plan_name}</p>
            <div className="gh-mock__btns">
              <Button block loading={busy === "paid"} disabled={!!busy || payment.status !== "pending"} onClick={() => finish("paid")}>
                Approve payment
              </Button>
              <Button block variant="secondary" disabled={!!busy || payment.status !== "pending"} onClick={() => finish("failed")}>
                Decline
              </Button>
            </div>
            {payment.status !== "pending" && <p className="gh-muted">This payment is already {payment.status}.</p>}
          </>
        )}
        <p className="gh-mock__note">This page simulates GCash for development. In production, buyers are sent to the real GCash checkout.</p>
      </div>
    </div>
  );
}
