import React from "react";
import { Link } from "react-router-dom";
import { usePageTitle } from "../hooks/hooks.js";
import { Accordion, Button, Container, SectionHeading, Section } from "../ui";
import { CtaBand, FAQ, FaqSection, Features, Hero, PricingSection, Workflow } from "../site/sections.jsx";
import PricingTable from "../site/PricingTable.jsx";

export function Home() {
  usePageTitle("");
  return (
    <>
      <Hero />
      <Features />
      <Workflow />
      <PricingSection />
      <FaqSection />
      <CtaBand />
    </>
  );
}

export function Pricing() {
  usePageTitle("Pricing");
  return (
    <>
      <Section labelledBy="pricing-page-title">
        <SectionHeading
          as="h1"
          no="$"
          eyebrow="Pricing"
          id="pricing-page-title"
          center
          title={<>Simple plans, paid with <em>GCash</em>.</>}
          lede="Prices are in Philippine pesos. Pro is prepaid for 30 or 365 days — no card needed and no automatic renewals."
        />
        <PricingTable />
      </Section>
      <Section tint labelledBy="pricing-faq">
        <div className="gh-faq">
          <SectionHeading no="?" eyebrow="Billing" id="pricing-faq" title={<>Billing, <em>explained</em>.</>} />
          <Accordion name="billing-faq" items={FAQ.slice(1, 4)} />
        </div>
      </Section>
    </>
  );
}

export function NotFound() {
  usePageTitle("Page not found");
  return (
    <Section labelledBy="nf-title">
      <SectionHeading as="h1" no="404" eyebrow="Off the sheet" id="nf-title" title={<>That page isn't on <em>this drawing</em>.</>}
        lede="The link may be old, or the address mistyped." />
      <Button to="/">Back to the homepage</Button>
    </Section>
  );
}

const Legal = ({ title, updated, children }) => {
  usePageTitle(title);
  return (
    <Section labelledBy="legal-title">
      <Container className="gh-legal">
        <SectionHeading as="h1" no="§" eyebrow={`Updated ${updated}`} id="legal-title" title={title} />
        <div className="gh-legal__body">{children}</div>
      </Container>
    </Section>
  );
};

const CONTACT = import.meta.env.VITE_CONTACT_EMAIL || "support@your-domain.example";

export function Terms() {
  return (
    <Legal title="Terms of Service" updated="October 2026">
      <p>
        These terms govern your use of Gearhead FEA (the "Service"). By creating an account you agree to them.
        <strong> Template notice:</strong> have these reviewed by qualified counsel and replace the operator details
        before launch.
      </p>
      <h2>1. The Service</h2>
      <p>
        Gearhead FEA provides browser-based finite element analysis tools. Results are estimates produced by a
        simplified linear solver and are provided for exploration and education. They are not a substitute for
        professional engineering verification, and you are responsible for any decisions made using them.
      </p>
      <h2>2. Accounts</h2>
      <p>
        You must provide a valid email address and keep your password confidential. You are responsible for activity
        on your account. We may suspend accounts that abuse the Service.
      </p>
      <h2>3. Plans and payment</h2>
      <p>
        The Free plan has the limits shown on the <Link to="/pricing">pricing page</Link>. Pro is a prepaid
        subscription period of 30 or 365 days, paid in Philippine pesos through GCash via our payment processor,
        PayMongo. Pro does <strong>not</strong> renew automatically; buying more time while Pro is active extends the
        end date. When Pro ends your account returns to the Free plan and your projects are retained.
      </p>
      <h2>4. Refunds</h2>
      <p>Refund terms: [operator to specify]. Contact us at {CONTACT} about billing problems.</p>
      <h2>5. Your content</h2>
      <p>You own the models and data you create. You grant us the limited right to store and process them to run the Service.</p>
      <h2>6. Availability and liability</h2>
      <p>
        The Service is provided "as is". To the extent permitted by law we are not liable for indirect or consequential
        losses arising from its use.
      </p>
      <h2>7. Contact</h2>
      <p>{CONTACT}</p>
    </Legal>
  );
}

export function Privacy() {
  return (
    <Legal title="Privacy Policy" updated="October 2026">
      <p>
        This policy explains what Gearhead FEA collects and why, consistent with the Philippine Data Privacy Act of 2012.
        <strong> Template notice:</strong> have this reviewed by qualified counsel and complete the operator details
        before launch.
      </p>
      <h2>What we collect</h2>
      <ul>
        <li>Account data: your name (optional), email address and a salted Argon2id hash of your password.</li>
        <li>Your projects: geometry, meshes, loads and results you create.</li>
        <li>Payment records: plan, amount, status and reference of each payment. We never receive your GCash PIN or wallet credentials; payment is completed on the processor's page.</li>
        <li>Technical data: IP address and browser type for security and abuse prevention, kept with your session.</li>
      </ul>
      <h2>How we use it</h2>
      <p>To run the Service, authenticate you, process payments, send transactional email (password resets and receipts), and keep the Service secure. We do not sell personal data.</p>
      <h2>Processors</h2>
      <p>PayMongo processes payments. Our hosting and email providers process data on our behalf under their own terms.</p>
      <h2>Cookies</h2>
      <p>We use one essential, HTTP-only session cookie to keep you signed in, and local storage to remember your theme. No advertising or tracking cookies.</p>
      <h2>Your rights</h2>
      <p>You may request access, correction or deletion of your data at {CONTACT}.</p>
    </Legal>
  );
}
