import React from "react";
import { Link } from "react-router-dom";
import { Accordion, Button, Card, Container, DimensionLine, Section, SectionHeading, Badge } from "../ui";
import { useAuth } from "../auth/AuthContext.jsx";
import MeshHero from "./MeshHero.jsx";
import PricingTable from "./PricingTable.jsx";
import "./site.css";

/* ------------------------------------------------------------------ Hero */
export function Hero() {
  const { user } = useAuth();
  return (
    <section className="gh-hero" aria-labelledby="hero-title">
      <Container className="gh-hero__inner">
        <div className="gh-hero__copy">
          <DimensionLine short>Finite element analysis</DimensionLine>
          <h1 id="hero-title" className="gh-hero__title">
            Test the part <em>before</em> you cut the metal.
          </h1>
          <p className="gh-hero__lede">
            Build a model, mesh it, pin it down, load it and watch the stress bloom across it — static, modal and
            thermal analysis with nothing to install.
          </p>
          <div className="gh-hero__cta">
            {user ? (
              <Button to="/app" size="lg">Open workspace</Button>
            ) : (
              <Button to="/register" size="lg">Start free</Button>
            )}
            <Button to="/pricing" variant="secondary" size="lg">See pricing</Button>
          </div>
          <ul className="gh-hero__facts" aria-label="At a glance">
            <li><strong>8</strong> analysis types</li>
            <li><strong>0</strong> installs</li>
            <li><strong>₱0</strong> to begin</li>
          </ul>
        </div>
        <MeshHero className="gh-hero__fig" />
      </Container>
    </section>
  );
}

/* -------------------------------------------------------------- Features */
const IconStatic = () => (
  <svg viewBox="0 0 96 56" className="gh-ficon" aria-hidden="true">
    <defs>
      <linearGradient id="gh-g1" x1="0" x2="1">
        <stop offset="0" stopColor="var(--gh-c5)" />
        <stop offset=".55" stopColor="var(--gh-c3)" />
        <stop offset="1" stopColor="var(--gh-c0)" />
      </linearGradient>
    </defs>
    <path d="M10 14h70l6 16H10z" fill="url(#gh-g1)" stroke="var(--gh-ink)" strokeWidth="2" strokeLinejoin="round" />
    <path d="M10 6v34M4 12l6 6M4 22l6 6M4 32l6 6" stroke="var(--gh-ink)" strokeWidth="2" fill="none" />
    <path d="M83 2v14m0 0-4-6m4 6 4-6" stroke="var(--gh-accent)" strokeWidth="2.4" fill="none" strokeLinecap="round" />
    <path d="M10 48h76" stroke="var(--gh-ink-3)" strokeWidth="1.5" strokeDasharray="3 3" />
  </svg>
);
const IconModal = () => (
  <svg viewBox="0 0 96 56" className="gh-ficon" aria-hidden="true">
    <path d="M6 28h84" stroke="var(--gh-ink-3)" strokeWidth="1.5" strokeDasharray="3 3" />
    <path d="M6 28C22 2 38 2 48 28S74 54 90 28" fill="none" stroke="var(--gh-c0)" strokeWidth="3" strokeLinecap="round" />
    <path d="M6 28C20 14 34 14 48 28S76 42 90 28" fill="none" stroke="var(--gh-accent)" strokeWidth="3" strokeLinecap="round" />
    <circle cx="48" cy="28" r="3.5" fill="var(--gh-ink)" />
  </svg>
);
const IconThermal = () => (
  <svg viewBox="0 0 96 56" className="gh-ficon" aria-hidden="true">
    <defs>
      <linearGradient id="gh-g3" x1="0" x2="1">
        <stop offset="0" stopColor="var(--gh-c5)" />
        <stop offset=".35" stopColor="var(--gh-c4)" />
        <stop offset=".65" stopColor="var(--gh-c2)" />
        <stop offset="1" stopColor="var(--gh-c0)" />
      </linearGradient>
    </defs>
    <rect x="8" y="12" width="80" height="32" fill="url(#gh-g3)" stroke="var(--gh-ink)" strokeWidth="2" />
    {[24, 40, 56, 72].map((x) => (
      <path key={x} d={`M${x} 12v32`} stroke="var(--gh-ink)" strokeOpacity=".28" strokeWidth="1" />
    ))}
    <path d="M8 28h80" stroke="var(--gh-ink)" strokeOpacity=".28" strokeWidth="1" />
  </svg>
);

const FEATURES = [
  {
    icon: <IconStatic />,
    title: "Static structural",
    tag: null,
    body: "Supports, forces, pressure, gravity and centrifugal loads — linear, or nonlinear with plasticity. Read stress, strain, reactions and displacement as colour contours.",
  },
  {
    icon: <IconModal />,
    title: "Modes & buckling",
    tag: "Pro",
    body: "Find the natural frequencies and buckling loads of a part and scrub through animated mode shapes to see exactly how it wants to move.",
  },
  {
    icon: <IconThermal />,
    title: "Heat transfer",
    tag: "Pro",
    body: "Fixed temperatures, heat sources, convection and radiation — steady or transient — plus thermal stress from the resulting field.",
  },
];

const EXTRAS = [
  ["Pick-and-place pre-processor", "Box, cylinder and sphere primitives with adjustable tetrahedral meshing. Click faces in 3D to select what you constrain and load."],
  ["A standalone solver", "Our own sparse FE engine — tetra and brick elements, linear and quadratic — runs on the server. Nothing else to install, nothing to license."],
  ["Your work, saved", "Projects live in your account, so your models, loads and results are there on any machine."],
];

export function Features() {
  return (
    <Section id="features" tint labelledBy="features-title">
      <SectionHeading
        no="01"
        eyebrow="Capabilities"
        id="features-title"
        title={<>Eight analyses. <em>One</em> workspace.</>}
        lede="The same loop engineers use in desktop pre/post-processors — model, constrain, solve, inspect — running in a tab."
      />
      <div className="gh-grid-3">
        {FEATURES.map((f) => (
          <Card key={f.title} as="article" className="gh-feature">
            {f.icon}
            <h3 className="gh-feature__title">
              {f.title} {f.tag && <Badge tone="accent">{f.tag}</Badge>}
            </h3>
            <p>{f.body}</p>
          </Card>
        ))}
      </div>
      <dl className="gh-extras">
        {EXTRAS.map(([t, d]) => (
          <div key={t}>
            <dt>{t}</dt>
            <dd>{d}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

/* -------------------------------------------------------------- Workflow */
const STEPS = [
  ["Model", "Pick a primitive, set its dimensions and choose a mesh density. A tetrahedral volume mesh is generated for you."],
  ["Constrain & load", "Click faces to select them, then pin them in place and apply forces, pressure, gravity or heat."],
  ["Solve", "One click. Jobs run on the server while a progress bar keeps you posted — typically seconds for a few thousand nodes."],
  ["Inspect", "Switch between stress, displacement and temperature contours, scale the deformation and animate mode shapes."],
];

export function Workflow() {
  return (
    <Section id="workflow" labelledBy="workflow-title">
      <SectionHeading
        no="02"
        eyebrow="Workflow"
        id="workflow-title"
        title={<>From blank sheet to <em>contour plot</em> in four moves.</>}
      />
      <ol className="gh-steps">
        {STEPS.map(([t, d], i) => (
          <li key={t} className="gh-step">
            <span className="gh-step__no" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
            <h3>{t}</h3>
            <p>{d}</p>
          </li>
        ))}
      </ol>
    </Section>
  );
}

/* --------------------------------------------------------------- Pricing */
export function PricingSection() {
  return (
    <Section id="pricing" tint labelledBy="pricing-title">
      <SectionHeading
        no="03"
        eyebrow="Pricing"
        id="pricing-title"
        center
        title={<>Pay in <em>pesos</em>, with GCash.</>}
        lede="Start free. Upgrade when you need nonlinear, modal, buckling, dynamic or thermal analysis, or more than a handful of projects."
      />
      <PricingTable />
    </Section>
  );
}

/* ------------------------------------------------------------------- FAQ */
export const FAQ = [
  {
    q: "Do I need to install anything?",
    a: "No. Gearhead FEA runs in a modern browser with WebGL. The standalone solver runs on our server, so even a modest laptop is fine.",
  },
  {
    q: "How does paying with GCash work?",
    a: "Choose Pro and you're taken to a secure GCash checkout hosted by our payment partner, PayMongo. Approve the payment in your GCash app, and you're sent straight back with Pro already active. We never see your GCash PIN or wallet details.",
  },
  {
    q: "Will you charge me automatically every month?",
    a: "No. Pro is prepaid: you buy 30 days or 365 days of access. GCash can't be billed silently, so nothing renews on its own. If you pay again while Pro is active, the new time is added to the end of what you have left.",
  },
  {
    q: "What happens when my Pro time runs out?",
    a: "You keep your account and every project. You return to the Free plan, so everything except static analysis pauses and you can't create projects beyond the Free limit until you renew.",
  },
  {
    q: "How accurate is the solver?",
    a: "The solver is checked against closed-form solutions (beam deflection, natural frequency and Euler buckling land within about 0.3% with quadratic elements). Linear tetrahedra and bricks are stiff in bending, so prefer the quadratic element types. Verify safety-critical parts independently and use your own engineering judgement.",
  },
  {
    q: "Who can see my projects?",
    a: "Only you. Projects belong to your account, passwords are stored as Argon2id hashes, and sessions use secure, HTTP-only cookies.",
  },
];

export function FaqSection() {
  return (
    <Section id="faq" labelledBy="faq-title">
      <div className="gh-faq">
        <SectionHeading no="04" eyebrow="Questions" id="faq-title" title={<>Straight <em>answers</em>.</>} />
        <Accordion items={FAQ} />
      </div>
    </Section>
  );
}

/* ------------------------------------------------------------------- CTA */
export function CtaBand() {
  const { user } = useAuth();
  return (
    <section className="gh-cta" aria-labelledby="cta-title">
      <Container className="gh-cta__inner">
        <h2 id="cta-title" className="gh-cta__title">Your first stress plot is a minute away.</h2>
        <div className="gh-cta__actions">
          {user ? (
            <Button to="/app" size="lg">Open workspace</Button>
          ) : (
            <Button to="/register" size="lg">Create a free account</Button>
          )}
          <Link to="/pricing" className="gh-cta__link">Compare plans →</Link>
        </div>
      </Container>
    </section>
  );
}
