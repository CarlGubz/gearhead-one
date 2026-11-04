# Implementation Log

Companion to [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). Everything below was done in this change set.

## Backend
- **Config** (`config.py`): env-driven settings; `validate_for_production()` blocks insecure production starts.
- **Database**: `database.py` now normalises `postgresql://` to `postgresql+psycopg://`, uses pre-ping pooling; SQLite remains for dev/tests.
- **Models**: `User`, `UserSession`, `PasswordReset`, `Subscription`, `Payment`; `Project.owner_id`. Idempotent migration adds `owner_id` (+index, + FK on PostgreSQL) to existing databases.
- **Security** (`security.py`): Argon2id, token generation + SHA-256 storage, password policy, timing-equalised verify.
- **Auth router**: register / login / logout / me / change-password / forgot / reset, lockout + rate limits.
- **Billing**: `plans.py`, `billing_service.py` (idempotent `mark_paid`, subscription stacking), `routers/billing.py`, `payments/` (interface, PayMongo, mock).
- **Existing API hardened**: every project/job route requires auth and ownership (404 otherwise); Free-plan limits (402); custom-mesh upload size cap; wildcard-CORS-with-credentials removed; Origin check, security headers, `/docs` off in production; `/api/health` checks the DB; project name length validation.
- **Mailer**: SMTP or console fallback.
- **Tests**: 32 pytest tests (`backend/tests`).

## Frontend
- Routing (`react-router-dom`), `AuthProvider`, route guards, safe `next` redirects, global 401/402 handling.
- Design system, tokens, light/dark themes, self-hosted fonts → see `DESIGN_SYSTEM.md`.
- Pages: Home, Pricing, Login, Register, Forgot/Reset password, Account & billing, Payment return (verifies + polls), Sandbox GCash, Terms, Privacy, 404.
- Workspace moved to lazy `/app` route with scoped legacy stylesheet; toolbar shows plan/email, Upgrade, Sign out; modal/thermal options labelled "Pro" for Free users.
- Favicon, robots.txt, meta/OG tags, per-page titles.

## Infrastructure
- `docker-compose.yml`: PostgreSQL 16 (healthcheck, volume) + backend (internal, non-root) + nginx frontend with CSP, SPA fallback, `/api` proxy.
- `.env.example`; `.gitignore` excludes `.env`.

## Verification performed
| Check | Result |
|---|---|
| `pytest` on SQLite | 32 passed |
| `pytest` on **PostgreSQL 16.4** (local binary) | 32 passed |
| Legacy-`projects`-table migration on PostgreSQL | owner_id/gravity columns added, row preserved |
| `vite build` | succeeds; landing JS ≈ 90 kB gz, workspace lazy chunk ≈ 234 kB gz |
| Browser run (Edge via Playwright): register → workspace → pricing → mock GCash approve → "You're on Pro" → account page → `/login` redirects signed-in user to `/app` | passes, no page errors |
| Screenshots reviewed at 1440 px (light + dark) and 390 px | layout OK |

## Not verified (be aware)
- **Docker images were not built** (Docker daemon unavailable here); Dockerfiles/compose/nginx config are unexecuted.
- **PayMongo live/test API was not called** (no credentials); only the mock provider and locally signed webhooks were exercised.
- E-mail delivery via real SMTP not tested (console fallback only).
- CSP in `nginx.conf` not tested against the 3-D workspace in a browser; if the viewport fails to render, relax `worker-src`/`img-src` first.
- The simulator's own panels were only smoke-tested (page loads, project panel renders); the FEA solver code is unchanged.
