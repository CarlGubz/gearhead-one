import React, { useEffect, useState } from "react";
import { getPlans } from "../api.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { formatDate, peso, useCheckout } from "../hooks/hooks.js";
import { Alert, Badge, Button, Card, Spinner } from "../ui";
import "./site.css";

function FeatureList({ items }) {
  return (
    <ul className="gh-feat-list">
      {items.map((f) => (
        <li key={f}>{f}</li>
      ))}
    </ul>
  );
}

/** Monthly / annual switch (radio semantics). */
function CycleSwitch({ value, onChange, savings }) {
  return (
    <div className="gh-cycle" role="radiogroup" aria-label="Billing period">
      {[
        ["pro_monthly", "Monthly"],
        ["pro_annual", "Annual"],
      ].map(([id, label]) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={value === id}
          className={`gh-cycle__opt${value === id ? " is-on" : ""}`}
          onClick={() => onChange(id)}
        >
          {label}
          {id === "pro_annual" && savings > 0 && <em>save {peso(savings)}</em>}
        </button>
      ))}
    </div>
  );
}

/**
 * Free vs Pro comparison with a billing-period switch. Used on the homepage
 * and /pricing; the Pro button starts a GCash checkout for signed-in users
 * and routes anonymous visitors through sign-up first.
 */
export default function PricingTable() {
  const { user, subscription, isPro } = useAuth();
  const { start, busyPlan, error } = useCheckout();
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [cycle, setCycle] = useState("pro_annual");

  useEffect(() => {
    getPlans().then(setData).catch(() => setFailed(true));
  }, []);

  if (failed) return <Alert tone="warn">Pricing is unavailable right now. Please try again shortly.</Alert>;
  if (!data) {
    return (
      <p className="gh-pricing__loading">
        <Spinner /> Loading plans…
      </p>
    );
  }

  const monthly = data.plans.find((p) => p.id === "pro_monthly");
  const annual = data.plans.find((p) => p.id === "pro_annual");
  const plan = cycle === "pro_annual" ? annual : monthly;
  const savings = monthly && annual ? monthly.amount * 12 - annual.amount : 0;
  const perMonth = plan.amount / (plan.days / 30);
  const isMock = data.payment_provider === "mock";

  return (
    <div className="gh-pricing">
      <CycleSwitch value={cycle} onChange={setCycle} savings={savings} />
      {error && <Alert tone="danger">{error}</Alert>}

      <div className="gh-pricing__grid">
        <Card className="gh-plan">
          <div className="gh-plan__head">
            <h3 className="gh-plan__name">Free</h3>
            {user && !isPro && <Badge>Your plan</Badge>}
          </div>
          <p className="gh-plan__price">
            <strong>{peso(0)}</strong> <span>forever</span>
          </p>
          <p className="gh-plan__tag">Everything you need to learn the workflow and check simple parts.</p>
          <FeatureList items={data.free_features} />
          <Button to={user ? "/app" : "/register"} variant="secondary" block className="gh-plan__cta">
            {user ? "Open workspace" : "Start free"}
          </Button>
        </Card>

        <Card accent className="gh-plan gh-plan--pro">
          <div className="gh-plan__head">
            <h3 className="gh-plan__name">Pro</h3>
            {plan.badge && <Badge tone="accent">{plan.badge}</Badge>}
            {isPro && <Badge tone="ok">Active</Badge>}
          </div>
          <p className="gh-plan__price" aria-live="polite">
            <strong>{peso(plan.amount)}</strong> <span>/ {plan.days === 365 ? "year" : "month"}</span>
          </p>
          <p className="gh-plan__tag">
            {plan.days === 365 ? `That's about ${peso(perMonth)} a month. ` : ""}
            {plan.tagline}
          </p>
          <FeatureList items={data.pro_features} />
          {user ? (
            <Button block loading={busyPlan === plan.id} onClick={() => start(plan.id)} className="gh-plan__cta">
              {isPro ? "Extend Pro with GCash" : "Pay with GCash"}
            </Button>
          ) : (
            <Button to={`/register?plan=${plan.id}`} block className="gh-plan__cta">
              Get Pro
            </Button>
          )}
          <p className="gh-plan__fine">
            {isPro && subscription?.current_period_end
              ? `Active until ${formatDate(subscription.current_period_end)}. Extending adds time on top.`
              : "Prepaid via GCash. No card, no auto-renewal — pay again only when you want more time."}
          </p>
          {isMock && <p className="gh-plan__fine gh-plan__fine--dev">Sandbox mode: no real money moves.</p>}
        </Card>
      </div>
    </div>
  );
}
