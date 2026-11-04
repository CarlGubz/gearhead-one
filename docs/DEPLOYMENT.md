# Deployment & Production Checklist

## Architecture
```
Internet ─TLS─▶ [load balancer / Caddy / platform ingress]
                         │ HTTP
                         ▼
                 nginx (frontend container)  ── serves SPA, security headers/CSP
                         │  /api/*
                         ▼
                 FastAPI (backend container, not published) ──▶ PostgreSQL
                                                    ▲
PayMongo ── POST /api/webhooks/paymongo ────────────┘ (through the same nginx)
```
The browser only ever talks to one origin, so the session cookie needs no CORS.

## Run with Docker Compose
```bash
cp .env.example .env          # edit: POSTGRES_PASSWORD, APP_BASE_URL, PayMongo keys…
docker compose up --build -d
```
Dev defaults: mock GCash, `http://localhost:5173`. The backend creates all tables on first start.

## Environment variables
| Variable | Purpose | Default |
|---|---|---|
| `APP_ENV` | `production` enables safety checks and hides `/docs` | `development` |
| `APP_BASE_URL` | public URL; payment redirects, e-mail links, Origin check | `http://localhost:5173` |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db` | SQLite file (dev/test only) |
| `PAYMENT_PROVIDER` | `paymongo` or `mock` | `mock` |
| `PAYMONGO_SECRET_KEY`, `PAYMONGO_WEBHOOK_SECRET` | PayMongo credentials | — |
| `ANTHROPIC_API_KEY` | enables the AI assistant (server-side only; empty = assistant off) | — |
| `AGENT_MODEL`, `AGENT_MAX_TURNS`, `AGENT_REQUESTS_PER_MINUTE` | assistant model, tool-loop cap, per-user rate limit | `claude-sonnet-5-5` / 14 / 12 |
| `COOKIE_SECURE` | force `Secure` cookies | true in production |
| `SMTP_HOST/PORT/USER/PASSWORD`, `MAIL_FROM` | password-reset & receipt mail (empty host = log to console) | — |
| `FREE_MAX_PROJECTS`, `SESSION_DAYS`, `MAX_FAILED_LOGINS`, `LOCKOUT_MINUTES`, `RESET_TOKEN_MINUTES` | tunables | 3 / 14 / 5 / 15 / 60 |
| `ALLOWED_ORIGINS` | extra CORS/Origin entries (comma-separated) | — |
| `VITE_CONTACT_EMAIL` (build) | shown in Terms/Privacy | — |

With `APP_ENV=production` the API **refuses to start** unless: DB is PostgreSQL, `APP_BASE_URL` is `https://`,
cookies are `Secure`, the provider is not `mock`, and PayMongo keys are set.

## CAD import (Gmsh)
`gmsh` is a normal Python dependency (`requirements.txt`). The API runs it in a child process (`python -m app.fea.gmsh_worker`) with a 180 s limit, so make sure
the backend image has `libglu1-mesa`, `libxrender1`, `libxcursor1`, `libxft2`, `libxinerama1`, `libfontconfig1` and `libgomp1` (the provided `Dockerfile` installs them -
**that image was not built or run in the development environment, so verify it once**). If Gmsh is missing the endpoint answers 501 with an install hint and
`/api/capabilities` reports `cad_import.available = false`. Gmsh is GPL-2+ (OpenCASCADE linking exception): review the licence before distributing the image.

## Go-live checklist
- [ ] Domain + TLS in front of nginx; `APP_BASE_URL=https://…`
- [ ] Strong `POSTGRES_PASSWORD`; DB on a managed/persistent volume; **backups + restore drill**
- [ ] PayMongo: business verified, GCash enabled, live keys, webhook registered (see `AUTH_AND_BILLING.md`), one real ₱ test payment settled
- [ ] SMTP configured; send a real password-reset
- [ ] Replace Terms/Privacy template text with counsel-reviewed text; set refund policy; set `VITE_CONTACT_EMAIL`
- [ ] Register with the NPC / appoint a DPO if required under the Data Privacy Act; consider BIR invoicing/VAT receipts
- [ ] Monitor `/api/health` (returns 503 if the DB is down) and webhook failures in PayMongo
- [ ] Run `pytest` against a PostgreSQL test database in CI

## Platform notes
- **DigitalOcean App Platform / Render / Fly**: one service from `backend/` (port 8000, internal) and the frontend nginx image as the public service; if the platform routes `/api` to the backend directly, nginx isn't needed — keep both under one domain. Set the env vars above.
- **Kubernetes**: two Deployments + Ingress (`/api` → backend, `/` → frontend). Don't expose the backend Service publicly except through `/api`.
- Behind any proxy the backend must see `X-Forwarded-For` (uvicorn runs with `--proxy-headers`) so rate limits key on the real client IP. Only run it where untrusted clients cannot reach the backend port directly.

## Known limitations / follow-ups
- Rate limiting is **per process**. With several API replicas use a shared store (Redis) or edge rate limiting. Account lockout is DB-based and already shared.
- Schema changes use `create_all` + a small idempotent migration step. Adopt **Alembic** before the next schema change.
- No e-mail verification, 2FA, social login or admin console yet.
- Jobs run in a background thread inside the API process; a worker queue would be the next step for heavy load.
- Refunds/chargebacks are handled in the PayMongo dashboard; the app does not yet reverse subscription time automatically.
- The simulator's compute limits (mesh density) are unchanged from v1.
