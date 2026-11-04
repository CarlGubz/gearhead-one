# Deploying Gearhead FEA on nginx (Ubuntu server, no Docker)

This guide puts the whole stack on one Linux server:

```
Internet ─HTTPS─▶ nginx (:443) ─┬─ /          → static React build  (/var/www/webfea)
                                └─ /api/      → uvicorn 127.0.0.1:8000 (systemd) ─▶ PostgreSQL (localhost)
```

The browser sees **one origin**, so the session cookie works without CORS.
If you would rather use Docker, see [`DEPLOYMENT.md`](DEPLOYMENT.md) (`docker compose up --build -d`) — this guide is the non-Docker alternative.

Assumes **Ubuntu 22.04/24.04**, a domain (`app.example.com` below) whose DNS A record points at the server, and a sudo user.
Replace `app.example.com` and every `CHANGE_ME` as you go.

---

## 1. Install system packages

```bash
sudo apt update
sudo apt install -y nginx postgresql python3 python3-venv python3-pip git curl ufw \
  libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1 libfontconfig1 libgomp1
```

The `lib*` packages are required by Gmsh (CAD import: STEP / IGES / STL). Node.js is needed only to *build* the frontend:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

Python 3.12 is what the project's Dockerfile uses. Check with `python3 --version`; on Ubuntu 22.04 install it from the `deadsnakes` PPA (`python3.12 python3.12-venv`) and use `python3.12` below.

## 2. Open the firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow "Nginx Full"     # 80 + 443
sudo ufw enable
```

Do **not** open port 8000 — the API must only be reachable through nginx (the app trusts `X-Forwarded-*` headers).

## 3. Create the database

```bash
sudo -u postgres psql <<'SQL'
CREATE USER webfea WITH PASSWORD 'CHANGE_ME_db_password';
CREATE DATABASE webfea OWNER webfea;
SQL
```

Production mode **refuses to start on SQLite**, so PostgreSQL is mandatory. Tables are created automatically on first start.

## 4. Get the code onto the server

```bash
sudo mkdir -p /opt/webfea && sudo chown $USER: /opt/webfea
git clone <your-repo-url> /opt/webfea          # or: rsync -av ./webfea/ user@server:/opt/webfea/
```

Do not copy `backend/.venv` or `frontend/node_modules` from Windows — they are rebuilt below.

## 5. Set up the backend

```bash
cd /opt/webfea/backend
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

Create a dedicated service user and the env file:

```bash
sudo useradd --system --home /opt/webfea --shell /usr/sbin/nologin webfea
sudo mkdir -p /etc/webfea
sudo nano /etc/webfea/webfea.env
```

`/etc/webfea/webfea.env`:

```ini
APP_ENV=production
APP_BASE_URL=https://app.example.com
DATABASE_URL=postgresql://webfea:CHANGE_ME_db_password@127.0.0.1:5432/webfea

# Real payments are mandatory in production (mock is rejected at startup)
PAYMENT_PROVIDER=paymongo
PAYMONGO_SECRET_KEY=sk_live_...
PAYMONGO_WEBHOOK_SECRET=whsk_...

# Password-reset + receipts (empty SMTP_HOST = mail only logged to console)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
MAIL_FROM=Gearhead FEA <no-reply@example.com>
```

Lock it down:

```bash
sudo chown root:webfea /etc/webfea/webfea.env && sudo chmod 640 /etc/webfea/webfea.env
sudo chown -R webfea: /opt/webfea/backend
```

> With `APP_ENV=production` the API will not start unless: the DB is PostgreSQL, `APP_BASE_URL` is `https://`, the payment provider is not `mock`, and the PayMongo keys are set. Cookies become `Secure` automatically, so the site only works over HTTPS (step 8).

## 6. Run the backend with systemd

`/etc/systemd/system/webfea.service`:

```ini
[Unit]
Description=Gearhead FEA API (uvicorn)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=webfea
Group=webfea
WorkingDirectory=/opt/webfea/backend
EnvironmentFile=/etc/webfea/webfea.env
ExecStart=/opt/webfea/backend/.venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 --workers 1 \
    --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=3
# CAD meshing and solves are CPU/RAM heavy: keep one runaway job from taking the box down
MemoryMax=6G

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now webfea
sudo systemctl status webfea --no-pager
curl -s http://127.0.0.1:8000/api/health        # expect a healthy JSON response
```

**Keep `--workers 1`.** Solve jobs run in background threads inside the API process and rate limiting is per-process; multiple workers would break job lookups and weaken rate limits. Tune `MemoryMax` to your server size.

If it fails to start, read the reason: `sudo journalctl -u webfea -n 50 --no-pager` (the production safety check prints a list of what is misconfigured).

## 7. Build and publish the frontend

```bash
cd /opt/webfea/frontend
npm ci
VITE_CONTACT_EMAIL=support@example.com npm run build     # output: frontend/dist
sudo mkdir -p /var/www/webfea
sudo rsync -a --delete dist/ /var/www/webfea/
sudo chown -R www-data: /var/www/webfea
```

`VITE_API_URL` defaults to `/api` (same origin) — leave it unset. `VITE_CONTACT_EMAIL` is baked in at build time and shown on the Terms/Privacy pages.

## 8. Configure nginx

`/etc/nginx/sites-available/webfea`:

```nginx
# Rate-limit nothing here by default; the API has its own limiter.
server {
    listen 80;
    listen [::]:80;
    server_name app.example.com;

    root /var/www/webfea;
    index index.html;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;

    # API + PayMongo webhook (/api/webhooks/paymongo) -> uvicorn
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 25m;        # matches the app's 25 MB upload limit (mesh / CAD / project files)
        proxy_read_timeout 120s;         # CAD import can run up to 180 s in the worker; raise to 200s if you use big CAD files
    }

    # Hashed build assets are immutable
    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        # add_header in a location drops the server-level ones, so repeat the security headers if you want them on assets
        add_header X-Content-Type-Options nosniff always;
        try_files $uri =404;
    }

    # SPA fallback (client-side routes such as /pricing, /account)
    location / {
        add_header Cache-Control "no-cache";
        add_header X-Content-Type-Options nosniff always;
        add_header X-Frame-Options DENY always;
        add_header Referrer-Policy strict-origin-when-cross-origin always;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
        try_files $uri /index.html;
    }
}
```

> **Why the repeated headers:** nginx does not inherit `add_header` from the `server` block into any `location` that defines its own `add_header`. The `/` location serves `index.html`, so it must carry the CSP itself.

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/webfea /etc/nginx/sites-enabled/webfea
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

At this point the site is reachable over plain HTTP. Production mode needs HTTPS, so continue immediately.

## 9. Enable HTTPS (Let's Encrypt)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d app.example.com --redirect -m you@example.com --agree-tos
sudo systemctl status certbot.timer          # auto-renewal is installed as a timer
sudo certbot renew --dry-run
```

Certbot edits the site file to add the `listen 443 ssl` block and an HTTP→HTTPS redirect. After it finishes, optionally add HSTS inside the `443` server block once you are sure HTTPS works:

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

Re-run `sudo nginx -t && sudo systemctl reload nginx`.

## 10. Verify

```bash
curl -I  https://app.example.com/                 # 200, security headers present
curl -s  https://app.example.com/api/health       # healthy JSON
curl -I  https://app.example.com/pricing          # 200 (SPA fallback works on deep links)
```

Then in the browser:

1. Open `https://app.example.com`, register an account, log in (checks the Secure cookie + DB).
2. Create a project, mesh a primitive, run a static solve (checks the engine + job threads).
3. Import a STEP/STL file (checks Gmsh). If it answers 501, the `lib*` packages from step 1 are missing; `GET /api/capabilities` shows `cad_import.available`.
4. Optional: create a Pro account without paying:
   ```bash
   cd /opt/webfea/backend && sudo -u webfea env $(grep -v '^#' /etc/webfea/webfea.env | xargs) \
     .venv/bin/python -m scripts.create_user you@example.com --pro-days 365
   ```

### PayMongo webhook

In the PayMongo dashboard → Developers → Webhooks, register:

- URL: `https://app.example.com/api/webhooks/paymongo`
- Event: `checkout_session.payment.paid`
- Copy the signing secret (`whsk_...`) into `PAYMONGO_WEBHOOK_SECRET`, then `sudo systemctl restart webfea`.

---

## Updating to a new version

```bash
cd /opt/webfea && git pull

# backend
cd backend && . .venv/bin/activate && pip install -r requirements.txt && deactivate
sudo systemctl restart webfea

# frontend
cd ../frontend && npm ci && npm run build
sudo rsync -a --delete dist/ /var/www/webfea/
```

New tables/columns are applied automatically on API start (`create_all` + a small idempotent migration). **Back up the database before upgrading.**

## Backups

```bash
# nightly dump (add to root's crontab: 0 3 * * * /usr/local/bin/webfea-backup.sh)
sudo -u postgres pg_dump -Fc webfea > /var/backups/webfea-$(date +%F).dump
# restore:  sudo -u postgres pg_restore -d webfea --clean /var/backups/webfea-YYYY-MM-DD.dump
```

Copy dumps off the server, and do a restore drill at least once.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `502 Bad Gateway` on `/api/*` | API is down: `systemctl status webfea`, `journalctl -u webfea -n 100`. |
| Service exits with "Refusing to start in production" | Fix the listed items in `/etc/webfea/webfea.env` (HTTPS URL, PostgreSQL, non-mock payments, PayMongo keys). |
| Can log in but get logged out / 401 on every request | Site is being used over plain HTTP while cookies are `Secure`; use HTTPS. Also check `APP_BASE_URL` matches the exact public URL (it is used for the Origin check). |
| `403` / origin errors on POST requests | `APP_BASE_URL` differs from the URL in the browser (e.g. `www.` vs bare domain). Fix it, or add the other origin to `ALLOWED_ORIGINS`. |
| `413 Request Entity Too Large` | `client_max_body_size` is missing from the `/api/` location (limit is 25 MB). |
| `504` during CAD import / long request | Increase `proxy_read_timeout` in `/api/`. |
| Blank page / console CSP errors | The `/` location lost the `Content-Security-Policy` header, or an external script/font was added (CSP only allows same-origin). |
| Deep link like `/account` gives 404 | The `try_files $uri /index.html;` fallback is missing. |
| CAD import returns 501 | Gmsh system libraries missing (step 1). |
| Payments stay pending | Webhook not reachable or wrong `PAYMONGO_WEBHOOK_SECRET`; check delivery logs in the PayMongo dashboard and `journalctl -u webfea`. |

## Production checklist

- [ ] DNS → server, HTTPS issued, HTTP redirects to HTTPS
- [ ] Strong DB password; nightly `pg_dump` copied off-server; restore tested
- [ ] `/etc/webfea/webfea.env` is `chmod 640`, not in git
- [ ] Port 8000 and 5432 not exposed publicly (`sudo ufw status`)
- [ ] PayMongo live keys + webhook registered; one real test payment settled
- [ ] SMTP works (send a real password reset)
- [ ] Uptime monitor on `https://app.example.com/api/health` (503 if the DB is down)
- [ ] Terms/Privacy text reviewed, `VITE_CONTACT_EMAIL` set (see [`DEPLOYMENT.md`](DEPLOYMENT.md) go-live list)
