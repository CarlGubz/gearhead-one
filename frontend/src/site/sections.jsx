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
          <DimensionLine short>CAD · CFD · CAE</DimensionLine>
          <h1 id="hero-title" className="gh-hero__title">
            Design it, flow it, stress it — <em>in one workspace.</em>
          </h1>
          <p className="gh-hero__lede">
            Gearhead One joins parametric CAD, computational fluid dynamics and finite element analysis around a single
            project. Model a part, send it to the solvers and read the results, with nothing to install.
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
            <li><strong>3</strong> engineering workspaces</li>
            <li><strong>0</strong> installs</li>
            <li><strong>₱0</strong> to begin</li>
          </ul>
        </div>
        <MeshHero className="gh-hero__fig" />
      </Container>
    </section>
  );
}

/* ----------------------------------------------------------------- Suite */
const IconCad = () => (
  <svg viewBox="0 0 96 56" className="gh-ficon" aria-hidden="true">
    <path d="M12 38 36 26l24 12-24 12z" fill="var(--gh-paper)" stroke="var(--gh-ink)" strokeWidth="2" strokeLinejoin="round" />
    <path d="M12 38V22l24-12 24 12v16M36 26V10" fill="none" stroke="var(--gh-ink)" strokeWidth="2" strokeLinejoin="round" />
    <path d="M66 14h22M66 14v-4m22 4v-4M66 30h22" stroke="var(--gh-accent)" strokeWidth="2" strokeLinecap="round" />
    <circle cx="77" cy="42" r="8" fill="none" stroke="var(--gh-accent)" strokeWidth="2" />
  </svg>
);
const IconCfd = () => (
  <svg viewBox="0 0 96 56" className="gh-ficon" aria-hidden="true">
    <rect x="6" y="8" width="84" height="40" fill="none" stroke="var(--gh-ink)" strokeWidth="2" />
    {[[14, "--gh-c0", 6], [22, "--gh-c2", 12], [34, "--gh-c4", 44], [42, "--gh-c5", 50]].map(([y, c, bend]) => (
      <path key={y} d={`M6 ${y}C34 ${y} 40 ${bend} 48 ${bend}S68 ${y} 90 ${y}`}
        fill="none" stroke={`var(${c})`} strokeWidth="2.6" strokeLinecap="round" />
    ))}
    <circle cx="48" cy="28" r="6" fill="var(--gh-ink)" fillOpacity=".2" stroke="var(--gh-ink)" strokeWidth="2" />
  </svg>
);
const IconCae = () => (
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

const SUITE = [
  {
    key: "cad",
    icon: <IconCad />,
    name: "CAD",
    title: "Design",
    to: "/app/cad",
    body: "Parametric 2-D sketches with a constraint solver, 3-D feature modelling, assemblies with mates, drawing sheets, and CAM toolpaths with G-code.",
    points: ["Sketch, extrude, revolve, fillet", "Assemblies and drawings", "CAM and G-code output"],
  },
  {
    key: "cfd",
    icon: <IconCfd />,
    name: "CFD",
    title: "Flow",
    to: "/app/cfd",
    body: "Set up a case, run it on the server and inspect velocity, pressure and temperature fields, with validated examples to start from.",
    points: ["Guided setup, mesh and run", "Live residual monitors", "OpenFOAM, FEniCSx and PyFR backends"],
  },
  {
    key: "cae",
    icon: <IconCae />,
    name: "CAE",
    title: "Stress",
    to: "/app",
    body: "Finite element analysis: constrain and load the model, then read stress, strain, reactions and displacement as colour contours.",
    points: ["Static, nonlinear, modal, buckling", "Steady and transient heat transfer", "Tet, brick, shell and beam elements"],
  },
];

export function Suite() {
  const { user } = useAuth();
  return (
    <Section id="suite" labelledBy="suite-title">
      <SectionHeading
        no="01"
        eyebrow="The suite"
        id="suite-title"
        title={<>Three disciplines. <em>One</em> project.</>}
        lede="Each workspace is a full tool on its own. Because they share a project, geometry and loads move between them instead of being rebuilt in a separate program."
      />
      <div className="gh-grid-3">
        {SUITE.map((p) => (
          <Card key={p.key} as="article" className="gh-feature">
            {p.icon}
            <h3 className="gh-feature__title">
              {p.name} <Badge tone="accent">{p.title}</Badge>
            </h3>
            <p>{p.body}</p>
            <ul className="gh-suite__points">
              {p.points.map((t) => <li key={t}>{t}</li>)}
            </ul>
            <Link to={user ? p.to : "/register"} className="gh-suite__link">
              {user ? `Open ${p.name}` : `Try ${p.name}`} →
            </Link>
          </Card>
        ))}
      </div>
    </Section>
  );
}

/* -------------------------------------------------------------- Features */
const EXTRAS = [
  ["One project, three views", "Geometry and loads transfer between CAD, CFD and FEA from the Transfer tool, so a design change reaches every analysis."],
  ["Real solvers, nothing to install", "Our own sparse FE engine, Gmsh, OpenCascade and CalculiX run on the server. Your browser only draws the results."],
  ["Your work, saved", "Projects live in your account, so your models, cases and results are there on any machine."],
];

export function Features() {
  return (
    <Section id="features" tint labelledBy="features-title">
      <SectionHeading
        no="02"
        eyebrow="Integration"
        id="features-title"
        title={<>Change the part once. <em>Re-run</em> everything.</>}
        lede="The usual loop of exporting STEP files, re-meshing and re-applying loads in three programs becomes one step in one tab."
      />
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
  ["Design in CAD", "Sketch and model the part, add mates and drawings, and generate toolpaths when it is ready to make."],
  ["Send it on", "Transfer the geometry to CFD and FEA. The mesh is built for you from the CAD model."],
  ["Flow and load", "Run the fluid case for pressures and temperatures, then constrain and load the structure."],
  ["Inspect and iterate", "Read the contours, go back to CAD, change a dimension and run the loop again."],
];

export function Workflow() {
  return (
    <Section id="workflow" labelledBy="workflow-title">
      <SectionHeading
        no="03"
        eyebrow="Workflow"
        id="workflow-title"
        title={<>From blank sheet to <em>verified design</em> in four moves.</>}
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
        no="04"
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
    a: "No. Gearhead One (CAD, CFD and FEA) runs in a modern browser with WebGL. The solvers run on our server, so even a modest laptop is fine.",
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
        <SectionHeading no="05" eyebrow="Questions" id="faq-title" title={<>Straight <em>answers</em>.</>} />
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
        <h2 id="cta-title" className="gh-cta__title">Your first design, flow and stress plot are a minute away.</h2>
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
