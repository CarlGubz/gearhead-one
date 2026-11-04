# Accounts & GCash Billing — Reference

## 1. Accounts (PostgreSQL)

### Tables (`backend/app/models.py`)
| Table | Purpose |
|---|---|
| `users` | `email` (unique, lower-case), `password_hash` (Argon2id), `failed_logins`, `locked_until`, `is_active` |
| `user_sessions` | server-side sessions; stores **SHA-256 of the cookie token**, expiry, IP, user-agent |
| `password_resets` | single-use, hashed, 60-minute reset tokens |
| `subscriptions` | one row per user: `plan_id`, `current_period_start/end` |
| `payments` | one row per checkout attempt: plan, amount (centavos), provider, `provider_ref`, `status` (`pending`/`paid`/`failed`) |
| `projects.owner_id` | every project belongs to a user (FK → `users.id`) |

Tables are created on startup (`create_all`); `_migrate_schema()` in `main.py` adds
`projects.owner_id` to older databases (legacy ownerless projects are hidden).

### Endpoints
| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register` | 201, signs in. Password policy: 10–128 chars, not trivial, not the e-mail name |
| POST | `/api/auth/login` | generic error for bad e-mail **or** password; locks account after 5 failures for 15 min |
| POST | `/api/auth/logout` | revokes the session |
| GET | `/api/auth/me` | user + subscription summary (401 if signed out) |
| POST | `/api/auth/change-password` | signs out every *other* session |
| POST | `/api/auth/forgot-password` | always 202 (no account enumeration); e-mails a link |
| POST | `/api/auth/reset-password` | single use; revokes all sessions |

### Security properties
- Cookie `gh_session`: `HttpOnly`, `SameSite=Lax`, `Secure` in production, 14-day sliding expiry.
- State-changing requests carrying an `Origin` header must match `APP_BASE_URL`/`ALLOWED_ORIGINS`/`Host` (CSRF defence in depth).
- Rate limits: 20 auth requests/min/IP, 5 reset requests/min/IP, 10 checkouts/min/IP (in-process; see DEPLOYMENT for multi-replica).
- Other users' projects/jobs/payments return **404**, never 403.
- Timing is equalised for unknown e-mails (dummy Argon2 verify).
- API docs (`/docs`) are disabled when `APP_ENV=production`.

## 2. Plans & limits (`backend/app/plans.py`)
| | Free | Pro |
|---|---|---|
| Projects | 3 (`FREE_MAX_PROJECTS`) | unlimited |
| Analyses | static | all 8: static, nonlinear, frequency, buckling, dynamic, thermo-mechanical, heat steady / transient |
| Price | ₱0 | ₱499 / 30 days · ₱4,990 / 365 days |

Enforced server-side: creating a project over the limit, switching a project to
modal/thermal, and solving a modal/thermal project all return **402**; the SPA shows an upgrade dialog.
A user is Pro while `subscriptions.current_period_end > now()`. Nothing "expires" via cron.

## 3. GCash payments

GCash has no direct merchant API; this project uses **PayMongo Checkout** restricted to
`payment_method_types: ["gcash"]`. The provider sits behind `payments/base.py`
(`create_checkout`, `fetch_status`) so another aggregator can be added without touching routes.

### Flow
```
SPA                    API                          PayMongo / GCash
 │ POST /billing/checkout {plan_id}                         │
 │───────────────────────▶ create Payment(pending)          │
 │                        └─ create checkout session ──────▶│
 │◀── {checkout_url} ─────┘                                 │
 │ browser → checkout_url ─────────────────────────────────▶│ user approves in GCash
 │◀──────────────── redirect /billing/return?payment=ID ────│
 │ POST /billing/payments/ID/verify ─▶ GET checkout session ▶│  (never trust the redirect)
 │                        ◀─ paid? ─┘ mark_paid() → extend subscription
 │                                  ◀── webhook checkout_session.payment.paid (signed)
 │                                      mark_paid() again → no-op
```

### Correctness guarantees
- **Idempotent**: `mark_paid` locks the payment row (`SELECT … FOR UPDATE`) and exits if already `paid`. Webhook retries and the verify call can race safely.
- **Amount check**: if the provider reports an amount different from the plan price, the payment is *not* settled.
- **Webhook authenticity**: HMAC-SHA256 of `"{t}.{raw body}"` with the webhook secret, compared (constant time) to `te` (test) or `li` (live). Test-mode events cannot settle a live-mode deployment.
- **Stacking**: paying while Pro is active starts the new period at the current end date.
- **No auto-renewal** (GCash can't be debited silently). Documented in the UI, FAQ and Terms.

### Endpoints
| Method | Path | Auth |
|---|---|---|
| GET | `/api/billing/plans` | public |
| GET | `/api/billing/subscription` | user |
| POST | `/api/billing/checkout` | user |
| GET | `/api/billing/payments` · `/payments/{id}` | user (own only) |
| POST | `/api/billing/payments/{id}/verify` | user (own only) |
| POST | `/api/webhooks/paymongo` | HMAC signature |
| POST | `/api/billing/mock/{id}/complete` | user; **404 unless `PAYMENT_PROVIDER=mock`** |

### Going live with PayMongo
1. Create a PayMongo account, complete business verification, **enable the GCash payment method**.
2. Set `PAYMENT_PROVIDER=paymongo`, `PAYMONGO_SECRET_KEY` (`sk_test_…` first, `sk_live_…` later).
3. In the PayMongo dashboard add a webhook → `https://<your-domain>/api/webhooks/paymongo`, event `checkout_session.payment.paid`; put its signing secret in `PAYMONGO_WEBHOOK_SECRET`.
4. Test with test keys (PayMongo's GCash test flow), confirm the payment appears as **paid** in *Account → Payment history*.
5. Swap to live keys and a live webhook secret. The app refuses to start in production without these.

### Local development without PayMongo
`PAYMENT_PROVIDER=mock` (default): "Pay with GCash" opens `/pay/mock/<id>`, an in-app screen with
**Approve / Decline**. No money moves. Production start-up **fails** if mock is selected.

> The PayMongo request/response shapes follow its public documentation but were **not exercised against
> the live API** in this build (no merchant credentials). The webhook signature code is unit-tested against
> locally generated signatures; run a PayMongo test-mode payment before launch (step 4 above).

## 4. Tests
`cd backend && pip install -r requirements-dev.txt && pytest tests` — 32 tests covering registration,
password policy, hashing, lockout, rate limit, session revocation, password reset, CSRF origin check,
project isolation, plan limits, mock checkout, double-settlement, early-renewal stacking, lapse to Free,
webhook signature/replay/amount/mode checks and the verify endpoint.
Run against PostgreSQL with `TEST_DATABASE_URL=postgresql://user:pass@host/db_test pytest tests`.
