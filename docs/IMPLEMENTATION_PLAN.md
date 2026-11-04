# Implementation Plan — Website, Accounts & GCash Subscriptions

Status of this document: **plan + decision record**. The companion file
[`IMPLEMENTATION_LOG.md`](IMPLEMENTATION_LOG.md) records what was actually
built and verified.

## 1. Starting point

WebFEA ("Gearhead FEA Suite") already ships a working simulation workspace:
FastAPI + SQLAlchemy backend (SQLite by default), React/Vite/Three.js
frontend, no authentication, no landing page, no billing. Every project is
visible to every visitor.

## 2. Goal

Turn the workspace into a production-ready product site:

1. A **homepage** with a distinctive visual identity.
2. A **login system** backed by **PostgreSQL**.
3. A **subscription payment system using GCash**.
4. Reusable frontend components throughout.
5. Everything documented.

## 3. Key decisions (and why)

| # | Decision | Reason |
|---|----------|--------|
| D1 | GCash is accepted through **PayMongo** (hosted Checkout, `gcash` payment method), behind a small provider interface. | GCash has no public direct-merchant API; production access is through licensed aggregators. PayMongo is PH-based and documents GCash + webhooks. The interface (`payments/base.py`) lets Xendit/Maya/Dragonpay be swapped in without touching routes. |
| D2 | Subscriptions are **prepaid periods** (30 days / 365 days), renewed by paying again — **no auto-debit**. | GCash e-wallet payments are one-off, customer-authorised redirects; they cannot be charged silently on a schedule. Paying while active *extends* the period from its current end, so early renewal loses nothing. |
| D3 | A **mock provider** (`PAYMENT_PROVIDER=mock`) with an in-app "GCash sandbox simulator" page. | Lets the entire flow be developed and tested without merchant credentials. It is **refused at startup when `APP_ENV=production`**. |
| D4 | Sessions are **opaque random tokens** in an `HttpOnly; SameSite=Lax; Secure` cookie; only the SHA-256 hash is stored in PostgreSQL. | Revocable (logout / password change), no JWT-secret management, token never readable by JavaScript (XSS-resistant), DB leak does not leak live sessions. |
| D5 | Passwords hashed with **Argon2id** (`argon2-cffi`). | Current OWASP recommendation. |
| D6 | Browser talks to the API **same-origin** (nginx serves SPA and proxies `/api`). | Cookies work without CORS-with-credentials; removes the wildcard-CORS+credentials hole in the old `main.py`. |
| D7 | Payment fulfilment is **idempotent** and triggered by two independent paths: signed webhook **and** server-side verification on return. | Webhooks can be delayed/lost; redirects can be forged. Both converge on one locked, status-checked function. |
| D8 | Free vs Pro gating: Free = 3 projects, static analysis only; Pro = unlimited projects + modal + thermal. | Gives the subscription real, enforceable meaning in the existing product. Limits live in one file (`plans.py`). |
| D9 | Schema changes via the existing `create_all` + idempotent `_migrate_schema()` pattern. | Matches the codebase; Alembic is listed as a follow-up in the deployment doc. |
| D10 | Brand name "Gearhead FEA"; visual identity = **engineering drawing sheet**. | See `DESIGN_SYSTEM.md`. |

## 4. Work breakdown

### 4.1 Backend
- [ ] `config.py` — typed env settings, production safety checks.
- [ ] `security.py` — Argon2id hashing, token generation/hashing, password policy.
- [ ] Models: `User`, `UserSession`, `PasswordReset`, `Subscription`, `Payment`; `Project.owner_id`.
- [ ] `routers/auth.py` — register, login, logout, me, change password, forgot/reset password.
- [ ] Brute-force protection: per-IP rate limit + per-account lockout (DB-backed).
- [ ] `plans.py` + `routers/billing.py` — plans, subscription status, checkout, verify, history, webhook, mock completion.
- [ ] `payments/` — provider interface, PayMongo client + webhook signature verification, mock provider.
- [ ] `mailer.py` — SMTP with console fallback (reset links, receipts).
- [ ] Lock down the existing project/job endpoints to the owner; enforce plan limits.
- [ ] Origin check for state-changing requests, security headers, strict CORS.
- [ ] Automated tests (pytest) for auth, ownership, plan limits, payments, webhook signature, idempotency.

### 4.2 Frontend
- [ ] Add routing, auth context, route guards, global 401/402 handling.
- [ ] Self-hosted fonts, design tokens, light ("vellum") + dark ("blueprint") themes.
- [ ] Reusable components (`ui/`, `layout/`, `brand/`) — see `DESIGN_SYSTEM.md`.
- [ ] Pages: Home, Pricing, Login, Register, Forgot/Reset password, Account & Billing, Checkout return, Mock GCash, Terms, Privacy, 404.
- [ ] Signature hero: live canvas FEA contour animation (cantilever beam).
- [ ] Move the existing workspace to a lazy-loaded `/app` route with its stylesheet scoped to that route; add account menu.
- [ ] SEO/meta, favicon, robots.txt, accessibility (focus, labels, reduced-motion).

### 4.3 Infrastructure & docs
- [ ] `docker-compose.yml` with PostgreSQL (healthcheck, volume), backend (internal only), nginx frontend.
- [ ] `.env.example`, nginx config with CSP and SPA fallback.
- [ ] Docs: this plan, implementation log, design system, auth & billing reference, deployment/production checklist; README updated.

## 5. Out of scope / follow-ups
Email verification, social login, 2FA, Alembic migrations, admin console,
refunds UI, invoices/VAT (BIR) formatting, multi-instance rate limiting
(Redis). These are called out in `DEPLOYMENT.md` so nothing is hidden.
