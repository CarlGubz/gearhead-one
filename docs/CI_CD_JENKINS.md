# CI/CD with Jenkins on a Linux server

A study guide **and** the runbook for the pipeline in [`Jenkinsfile`](../Jenkinsfile). Goal: every push to `main`
is built, tested and deployed to your own Linux server automatically; every other branch is built and tested only.

> The pipeline was written against this repository's real files (`docker-compose.yml`, both Dockerfiles, `.env.example`,
> `/api/health`) but has **not been run yet**. Expect to fix small things on the first run; section 11 lists the likely ones.

---------------------------------------------------------------------------------------------------

## 1. Concepts in five minutes

| Term | Meaning |
|---|---|
| **CI** (continuous integration) | Every change is automatically built and tested, so a broken change is noticed in minutes, not weeks. |
| **CD** (continuous delivery / deployment) | A change that passed CI is automatically released. *Delivery* = ready to release with one click; *deployment* = released with no click. This pipeline does **deployment** on `main`. |
| **Jenkins** | A server that watches your repository and runs your pipeline whenever something changes. |
| **Pipeline** | The recipe, written as code in a `Jenkinsfile` stored in the repo (so it is versioned and reviewed like any other code). |
| **Controller / agent** | The controller is the Jenkins web app and scheduler. An agent is a machine that executes the steps. Here one machine plays both roles (`agent any`). |
| **Stage / step** | A stage is a named phase (Build, Test, Deploy). A step is one command inside it (`sh '...'`). |
| **Workspace** | The folder where Jenkins checks out your code for a build: `/var/lib/jenkins/workspace/<job>`. |
| **Credential** | A secret stored encrypted in Jenkins and handed to a build only when asked for. Never put secrets in the `Jenkinsfile`. |
| **Webhook** | Your git host calls Jenkins the moment you push, instead of Jenkins asking every few minutes ("polling"). |

## 2. What this application needs

Look at what the repo already gives us; the pipeline just automates it.

| Part | How it is built / run | CI/CD consequence |
|---|---|---|
| Backend (FastAPI + NumPy/SciPy solver + Gmsh) | `backend/Dockerfile`, tests in `backend/tests` run with `pytest` (459 tests, SQLite, no network or API key) | Build the image, run the tests *inside* it so they see exactly the production libraries (Gmsh needs system libs). |
| Frontend (React + Vite) | `frontend/Dockerfile` does `npm ci` and `npm run build`, then serves with nginx | Building the image is the frontend "test": a compile error fails the build. There is no frontend test suite. |
| Database | PostgreSQL 16 container, data in the named volume `pgdata` | Must **survive** deploys: never use `docker compose down -v`. |
| Stack | `docker-compose.yml` (db + backend + frontend). Only the frontend publishes a port (`HTTP_PORT`, default 5173); nginx proxies `/api` to the backend | Deploy = `docker compose up -d --build`. |
| Config & secrets | `.env` next to the compose file (see `.env.example`). Git-ignored | Jenkins injects it from a credential at deploy time. |
| Health | `GET /api/health` | Used as the smoke test after a deploy. |

## 3. The pipeline at a glance

```
 you push ──▶ git host ──webhook──▶ Jenkins (your server)
                                       │
        ┌──────────────────────────────┘
        ▼
   Preflight        docker / compose / git are present
   Build images     backend image ║ frontend image      (parallel; frontend build = compile check)
   Backend tests    pytest inside the backend image, JUnit report published
        │
        │  only on main and DEPLOY=true
        ▼
   Deploy           .env from credential ─▶ docker compose up -d --build
   Smoke test       curl /api/health until it answers (max ~90 s)
   post             on failure: show container logs;  always: delete .env, prune images
```

If any stage fails, Jenkins stops there and the running site is **not touched**. That is the whole point: tests sit
in front of the deploy.

## 4. The Jenkinsfile, line by line

| Block | What it does and why |
|---|---|
| `agent any` | Run on any node. With one server that is the server itself. |
| `options { timestamps(); timeout(45 min); buildDiscarder(20); disableConcurrentBuilds() }` | Timestamps in the log; a hung build is killed after 45 minutes; only the last 20 builds are kept (disk); two builds never run at once, so two deploys cannot collide. |
| `triggers { pollSCM('H/5 * * * *') }` | Safety net: check the repo every ~5 min (`H` spreads the load). Remove it once webhooks work. |
| `parameters { DEPLOY }` | A checkbox in *Build with Parameters*. Untick to run only CI on `main`. |
| `environment` | `COMPOSE_PROJECT_NAME=webfea` makes compose use the same container names and the same `webfea_pgdata` volume every time. Image names carry the build number (`webfea-backend:ci-17`) so concurrent or old images never clash. |
| **Preflight** | Fails in seconds with a clear message if Docker or Compose is missing, instead of failing in the middle of a build. |
| **Build images** (parallel) | `docker build` of both images. Docker's layer cache makes repeat builds fast when `requirements.txt` / `package-lock.json` did not change. |
| **Backend tests** | `docker run` of the image with `backend/tests` mounted read-only (tests are excluded from the image by `.dockerignore`), installs `pytest` for the non-root user and writes a JUnit XML into `test-results/`. `junit` in `post { always }` publishes it, so Jenkins shows pass/fail counts and trends. |
| **Deploy** `when {…}` | Runs only if `DEPLOY` is true **and** the branch is `main` (`BRANCH_NAME` in a multibranch job, `GIT_BRANCH` in a plain pipeline job). |
| `withCredentials([file(...)])` | Jenkins writes the secret file to a temporary path (`$ENV_FILE`) for this block only; we copy it to `.env` with mode 600. The value never appears in the log (Jenkins masks it). |
| `docker compose config --quiet` | Validates the compose file and that all required variables (`POSTGRES_PASSWORD`) exist; fails early with a readable error. |
| `docker compose up -d --build --remove-orphans` | Rebuilds changed images and recreates only the containers whose image/config changed. `db` is untouched, so data is kept. |
| **Smoke test** | A deploy that "started" is not a deploy that "works". We call the real endpoint, which also touches the database. 30 tries × 3 s. |
| `post { failure }` | Prints `compose ps` and the last backend log lines so you see the cause in the build log. |
| `post { cleanup }` | Always runs last: removes `.env` from the workspace, removes this build's tagged images and prunes dangling layers. |

## 5. Server preparation (Ubuntu/Debian; adapt for others)

Assumptions: a Linux server you control with sudo, a DNS name (optional but recommended), the app and Jenkins on the **same** machine.
Rough size: 2 CPU, 4 GB RAM, 30 GB disk (Docker images and Gmsh are not small).

### 5.1 Docker Engine and Compose plugin
Follow <https://docs.docker.com/engine/install/> for your distribution (for a throw-away learning server,
`curl -fsSL https://get.docker.com | sudo sh` is the quick way). Verify:
```bash
docker --version && docker compose version
```

### 5.2 Java and Jenkins
Jenkins needs Java 17 or 21:
```bash
sudo apt update && sudo apt install -y fontconfig openjdk-21-jre git curl
java -version
```
Install Jenkins **from the official apt repository** by copying the current commands from
<https://www.jenkins.io/doc/book/installing/linux/> (the signing-key file name changes yearly, so do not copy it from an old tutorial). The result is:
```bash
sudo apt install -y jenkins
sudo systemctl enable --now jenkins
sudo systemctl status jenkins          # should say active (running)
```
Jenkins listens on port **8080**. The first admin password:
```bash
sudo cat /var/lib/jenkins/secrets/initialAdminPassword
```

### 5.3 Let Jenkins use Docker
```bash
sudo usermod -aG docker jenkins
sudo systemctl restart jenkins
sudo -u jenkins docker ps               # must work without sudo
```
> **Security, read this once:** membership of the `docker` group is effectively root on that machine. Anyone who can
> change the `Jenkinsfile` can run anything as root through Docker. So: only trusted people may push to branches that Jenkins builds,
> do not build untrusted pull requests, and keep Jenkins itself updated and behind a login (section 10).

### 5.4 Firewall and access
- Open the app port (`HTTP_PORT`, e.g. 80 or 5173) to users.
- Do **not** expose 8080 to the whole internet. Either restrict it by IP (`sudo ufw allow from <your-ip> to any port 8080`), reach it through an SSH tunnel (`ssh -L 8080:localhost:8080 you@server`), or put it behind HTTPS (section 10).
- If you use a git host that must reach Jenkins for webhooks (GitHub/GitLab cloud), 8080 (or your HTTPS proxy) must be reachable from them; self-hosted git on the same LAN needs nothing public.

### 5.5 HTTPS for the app
The frontend container only speaks HTTP (`nginx.conf` says TLS is expected to terminate in front). On a real server run a reverse proxy
such as **Caddy** or nginx on the host that terminates TLS and forwards to `localhost:<HTTP_PORT>` while keeping `X-Forwarded-Proto`.
In `.env` set `APP_ENV=production`, `APP_BASE_URL=https://your.domain`, `COOKIE_SECURE=true`, a real payment provider and `HTTP_PORT` to a port the proxy uses
(production mode refuses mock payments, SQLite, `http://` URLs and insecure cookies).

## 6. Configure Jenkins

1. Open `http://<server>:8080`, paste the initial password, choose **Install suggested plugins**, create your admin user.
2. Required plugins (all part of "suggested"): *Pipeline*, *Git*, *Credentials Binding*, *JUnit*, *Timestamper*, *Workspace Cleanup*.
   For a GitHub/GitLab webhook add *GitHub* or *GitLab* plugin; for multibranch with GitHub add *GitHub Branch Source*.
3. **Manage Jenkins → Credentials → System → Global → Add credentials**
   - *Kind*: **Secret file**, *File*: your production `.env`, *ID*: **`webfea-env`** (must match `ENV_CREDENTIAL_ID` in the Jenkinsfile), a description.
   - If the repository is private add a second credential (*Username with password* using a deploy token, or *SSH Username with private key* with a read-only deploy key) with the ID `git-readonly`.

### 6.1 Production `.env` (what goes in the secret file)
Start from `.env.example`:
```dotenv
POSTGRES_PASSWORD=<long random>              # openssl rand -base64 32
APP_ENV=production
APP_BASE_URL=https://your.domain
HTTP_PORT=8081                               # the port your TLS proxy forwards to
CONTACT_EMAIL=support@your.domain
PAYMENT_PROVIDER=paymongo
PAYMONGO_SECRET_KEY=sk_live_...
PAYMONGO_WEBHOOK_SECRET=whsk_...
COOKIE_SECURE=true
SMTP_HOST=...  SMTP_PORT=587  SMTP_USER=...  SMTP_PASSWORD=...  MAIL_FROM=...
GEMINI_API_KEY=...                            # optional: enables the AI Assistant
```
Rotating a secret = edit the credential (**Update → Replace** the file), then run the job. Remember `POSTGRES_PASSWORD` is only used the first time the
database volume is created; changing it later does not change the existing database user, so change it inside PostgreSQL as well.

### 6.2 Create the job — pick one

**A. Multibranch Pipeline (recommended).** *New Item → Multibranch Pipeline*. *Branch Sources → Git* (or GitHub), repository URL, credential `git-readonly`.
Build Configuration: *by Jenkinsfile*, script path `Jenkinsfile`. Jenkins then creates one job per branch automatically: feature branches run CI, `main` runs CI + deploy.

**B. Single Pipeline job.** *New Item → Pipeline*. *Pipeline script from SCM → Git*, repository URL, credential, **branch `*/main`**, script path `Jenkinsfile`. Simpler, only builds `main`.

> Put the project under git first (this directory is not a repository yet): `git init`, commit, add a remote, push. `.gitignore` already excludes `.env`, `*.db`, `node_modules/` and `dist/`.
> Also make sure `backend/new_points.pos` and `backend/webfea.db` are not committed unless you want them.

### 6.3 Triggers
- **Webhook (best, instant).** GitHub: repo *Settings → Webhooks → Add*, payload URL `http://<jenkins>/github-webhook/`, content type `application/json`, event *push*. GitLab: *Settings → Webhooks*, URL from the GitLab plugin's job setting.
  In the job tick *GitHub hook trigger for GITScm polling* (single pipeline) – multibranch handles it itself.
- **Polling (works anywhere).** The `pollSCM` line in the Jenkinsfile. Costs a little CPU and a delay of up to 5 minutes.

## 7. Your first run

1. Open the job → **Build Now** (multibranch: open the `main` branch first).
2. Watch **Console Output**. Typical durations: first build 5–10 min (downloads base images, installs SciPy/Gmsh/npm packages); later builds 1–3 min thanks to layer caching, plus the test time.
3. After **Backend tests** a *Test Result* link appears on the build page.
4. After the **Smoke test**: `curl http://localhost:<HTTP_PORT>/api/health` on the server, then open the site in a browser.
5. Prove the loop works: change a text in the frontend, commit, push, and watch the new build start and the site change without you touching the server.

## 8. Day-to-day operations

| Task | Command / action |
|---|---|
| See what is running | `cd /var/lib/jenkins/workspace/<job> && docker compose ps` or `docker ps` |
| App logs | `docker compose -p webfea logs -f backend` |
| Redeploy without a code change | Jenkins → *Build with Parameters* (DEPLOY ticked) |
| Run CI only on `main` | *Build with Parameters*, untick DEPLOY |
| **Roll back** | `git revert <bad-commit>` and push (the pipeline redeploys the previous state, tests included). Emergency alternative: re-run an older successful build's commit via *Replay* / *Rebuild* of that build. |
| Stop the app | `docker compose -p webfea stop` |
| Database backup | `docker compose -p webfea exec -T db pg_dump -U webfea webfea > webfea-$(date +%F).sql` (schedule it with cron; copy off the server) |
| Database restore | `docker compose -p webfea exec -T db psql -U webfea webfea < backup.sql` |
| Free disk space | `docker system df`, then `docker image prune -a` (the pipeline already prunes dangling layers) |
| Back up Jenkins | Archive `/var/lib/jenkins` (jobs, credentials, config) regularly. |

**Never** run `docker compose down -v` on the server: `-v` deletes the `pgdata` volume, i.e. all users, projects and payments.

## 9. Design decisions and trade-offs (good exam questions)

- **Jenkins on the host, not in a container.** Pipeline steps call `docker` directly and bind-mount workspace folders (`-v "$PWD/backend/tests:…"`). With Jenkins inside Docker the workspace path differs between Jenkins and the Docker daemon, which breaks such mounts unless you add Docker-in-Docker or path mapping. Host install is the simplest correct choice for one server.
- **Tests inside the production image** (instead of `pip install` in the workspace): same Python, same Gmsh libraries, no virtualenv to manage on the server.
- **Rebuild on deploy (`up --build`)** instead of a registry: with one server there is nothing to pull from. With several servers you would push images to a registry (GHCR, Docker Hub, a private one) and deploy by tag - the natural next step.
- **Compose recreate = a few seconds of downtime** for the containers that changed. Zero-downtime (blue/green) needs a second copy plus a load balancer; not worth it yet.
- **Secrets as a Jenkins file credential** rather than a file on disk: they live encrypted, are masked in logs and are copied into the workspace only during the build, then deleted in `post { cleanup }`.
- **Deploy gate = branch + parameter.** Feature branches get CI only; nothing reaches production without passing the tests on `main`. Protect `main` in your git host (pull requests required) so this stays true.
- **Not covered yet:** frontend unit tests/lint (the repo has none), image vulnerability scanning (e.g. Trivy), notifications (email/Slack on failure), automatic database migrations (the app creates its own tables at start).

## 10. Hardening checklist

- [ ] Jenkins reachable only over HTTPS (reverse proxy with a certificate) or an SSH tunnel; 8080 not open to the world.
- [ ] **Manage Jenkins → Security**: "Jenkins' own user database", sign-up disabled, "Logged-in users can do anything" or matrix-based authorization; CSRF protection on (default).
- [ ] Controller built-in node has **0 executors** if you later add separate agents.
- [ ] Only trusted contributors can push to branches that Jenkins builds (Docker group = root).
- [ ] Credentials are scoped and rotated; deploy key is read-only.
- [ ] Jenkins and plugins updated regularly (**Manage Jenkins → Plugins**).
- [ ] Backups of `/var/lib/jenkins` and of the PostgreSQL database tested by an actual restore.

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `permission denied … /var/run/docker.sock` | `jenkins` not in the docker group, or Jenkins not restarted after `usermod`. |
| `docker: 'compose' is not a docker command` | Install the Compose plugin (`docker-compose-plugin`) - the pipeline uses `docker compose` (v2), not `docker-compose`. |
| `couldn't find credentials 'webfea-env'` | Credential ID differs from `ENV_CREDENTIAL_ID`, or it is not in a scope visible to the job. |
| `required variable POSTGRES_PASSWORD is missing` | The secret file lacks it (or has Windows line endings: use `dos2unix`). |
| Backend tests fail on a permission error | The tests/pip run as uid 10001. Check the line `chmod 777 test-results` ran and the error path; if a test writes to `/app`, point it at `/tmp` or run the container with `--tmpfs /app/data:uid=10001`. |
| Tests pass locally, fail in Jenkins | Compare Python versions (image is 3.12) and look for tests that rely on a local `.env` or `webfea.db`. |
| Smoke test "health check failed" | Read the failure log block; typical: wrong `HTTP_PORT` in `.env`, `APP_ENV=production` with mock payments/`http://` URL (the backend refuses to start - see its log), or the database is still initialising (raise the retries). |
| Port already in use | Another service uses `HTTP_PORT`; change it in the secret file. |
| Build never starts after a push | Webhook not delivered (check the git host's "Recent deliveries") or polling interval not reached; the Jenkins *Git Polling Log* shows what it saw. |
| Disk full | `docker system df`; prune; reduce `numToKeepStr`. |
| `junit` finds no files | Tests crashed before writing the report; the console above it shows why (`allowEmptyResults` keeps the build from failing twice). |

## 12. Exercises (to learn by doing)

1. Break a test on a feature branch; confirm Jenkins fails at **Backend tests** and the site is unchanged. Fix it and watch it go green.
2. Add `trivy image "$BACKEND_IMAGE"` as a stage and decide what severity should fail the build.
3. Add a `post { failure { mail … } }` or a Slack notification.
4. Replace polling with a webhook and measure the delay.
5. Push images to a registry and make the Deploy stage pull a tag instead of building.
6. Schedule the `pg_dump` backup as a second Jenkins job with `cron('H 3 * * *')`.
7. Add a manual approval before deploy: `input message: 'Deploy to production?'`.

## 13. Glossary of commands used
- `docker build -t NAME DIR` - build an image from `DIR/Dockerfile`.
- `docker run --rm -v HOST:CONTAINER IMAGE CMD` - run a command in a throw-away container with a folder shared in.
- `docker compose up -d --build` - build changed images, create/update containers, run them in the background.
- `docker compose config --quiet` - validate the compose file and its variables.
- `docker image prune -f` - delete unused untagged image layers.
- `install -m 600 SRC DST` - copy a file and set owner-only permissions in one step.
- `curl -fsS URL` - fetch quietly; `-f` makes HTTP errors a non-zero exit, which fails the stage.
