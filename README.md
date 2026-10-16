# Mini PaaS

A small, self-hosted Platform-as-a-Service in the spirit of Render, Railway and Heroku.
Upload a ZIP or point to a public Git repository; the platform detects the runtime, builds a
Docker image, runs it in an isolated, resource-limited container and serves it at
`http://<your-app>.localhost` — with live build/runtime logs, metrics, environment variables,
deployment history and one-click rollback.

```bash
cp .env.example .env
docker compose up --build
# open http://localhost
```

---

## Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Features](#features)
4. [Requirements](#requirements)
5. [Installation](#installation)
6. [Environment variables](#environment-variables)
7. [Docker services](#docker-services)
8. [Database and migrations](#database-and-migrations)
9. [Development](#development)
10. [Testing](#testing)
11. [Security](#security)
12. [Deploying to a server](#deploying-to-a-server)
13. [Troubleshooting](#troubleshooting)
14. [Known limitations](#known-limitations)
15. [Roadmap](#roadmap)

---

## Overview

| You do                                   | The platform does                                                                 |
|------------------------------------------|-----------------------------------------------------------------------------------|
| Register and create a project            | Reserves `<slug>.localhost`, applies CPU/RAM limits                               |
| Upload a `.zip` or give a Git URL        | Validates the archive (zip bombs, traversal, symlinks) or clones safely           |
| Press **Deploy**                         | Detects Python / Node.js / .NET / static / Dockerfile, generates a Dockerfile, builds it |
|                                          | Starts a hardened container, health-checks it, switches traffic, stops the old one |
| Watch the **Logs** tab                   | Streams build, deploy and application output live over WebSockets                 |
| Add `DATABASE_URL`, `SECRET_KEY`, …      | Stores them encrypted, injects them at the next deploy                           |
| Click **Restart / Stop / Start**         | Manages the container lifecycle                                                   |
| Pick an old deployment → **Roll back**   | Redeploys its image without rebuilding                                            |

The stack: **FastAPI** (async API), **SQLAlchemy 2 + Alembic** on **PostgreSQL**, **Redis**
(Celery broker, pub/sub, rate limits), **Celery** workers, a runtime **agent**, **Traefik**
as the edge proxy, and a **React + TypeScript + Vite + Tailwind + Zustand** dashboard.

## Architecture

```
                  ┌──────────────┐
                  │   Browser    │   http://localhost  ·  http://<slug>.localhost
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐  file provider: platform routes
                  │   Traefik    │  http provider: app routes rendered by the API from PostgreSQL
                  └──────┬───────┘
                ┌────────┴─────────┬──────────────────────────┐
                ▼                  ▼                          ▼  one network per project
        ┌──────────────┐   ┌──────────────┐          ┌─────────────────┐
        │  Frontend    │   │   FastAPI    │          │ user containers │ cap_drop ALL, non-root,
        │ nginx + SPA  │   │  REST + WS   │          │                 │ read-only, cpu/mem/pids
        └──────────────┘   └──────┬───────┘          └─────────────────┘ limits, no socket
                                  │                           ▲
                    ┌─────────────┼─────────────┐             │ Docker API (worker + agent only)
                    ▼             ▼             ▼             │
               PostgreSQL       Redis ───► Celery workers ────┤  build · deploy · stop · rollback
                                  ▲        (+ beat)           │
                                  └─────── Runtime agent ─────┘  logs · docker stats · crash watch
```

```mermaid
sequenceDiagram
    participant U as Browser
    participant A as API
    participant Q as Redis
    participant W as Worker
    participant D as Docker
    participant T as Traefik
    U->>A: POST /api/projects/{id}/upload (zip)
    A->>A: validate archive · INSERT deployment (QUEUED)
    A->>Q: enqueue deployments.run
    A-->>U: 202 Accepted
    W->>D: BUILDING: extract · detect runtime · docker buildx build
    W->>D: DEPLOYING: network + hardened container
    W->>D: HEALTH_CHECKING: HTTP probes inside the project network
    W->>A: RUNNING (DB) — current deployment switched
    T->>A: poll /internal/traefik/config (2 s)
    W->>D: stop previous container
    W-->>Q: logs + status events (pub/sub)
    Q-->>U: WebSocket /ws/deployments/{id}/logs · /ws/events
```

Design decisions (why Traefik, why the Docker socket only lives in the worker, why BuildKit via
the CLI, why one network per project, async API + sync workers, DB-enforced concurrency…) are
recorded as ADRs in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Repository layout

```
mini-paas/
├── backend/
│   ├── app/
│   │   ├── api/            routers (no business logic), dependencies, WebSockets
│   │   ├── core/           settings, logging, errors, security, crypto, rate limiting
│   │   ├── db/             engine/session, base model, enum type
│   │   ├── models/         SQLAlchemy models
│   │   ├── schemas/        Pydantic DTOs
│   │   ├── repositories/   queries (always scoped by owner)
│   │   ├── services/       business logic for the API
│   │   ├── deployments/    state machine, health checks, naming, events
│   │   ├── builds/         archive safety, git source, runtime detection, Dockerfiles, BuildService
│   │   ├── docker/         DockerService (the only Docker client) + stats parsing
│   │   ├── routing/        DomainService, Traefik config rendering
│   │   ├── logs/           LogSink (batched persistence + pub/sub)
│   │   ├── workers/        Celery app, tasks, pipeline, lifecycle, maintenance, locks
│   │   ├── agent/          runtime log follower, metrics, crash watcher
│   │   └── cli/            seed, secret generation
│   ├── alembic/            migrations
│   └── tests/              unit + integration (PostgreSQL + Redis)
├── frontend/               React SPA (components, pages, layouts, hooks, services, stores, types)
├── e2e/                    Playwright end-to-end tests
├── examples/               python (FastAPI), node (Express), static, dockerfile
├── infrastructure/         traefik config, host hardening script
├── docs/ARCHITECTURE.md
├── docker-compose.yml · Makefile · .env.example · .github/workflows/ci.yml
```

## Features

- **Auth** — register, login, logout, `GET /auth/me`; Argon2id hashes; 15-minute JWT access
  tokens kept in memory; rotating httpOnly refresh cookie with reuse detection; single-use
  WebSocket tickets.
- **Multi-tenancy** — every query is scoped to its owner; another user's resources answer `404`.
- **Projects** — name, validated unique slug (reserved words, length, charset), description,
  runtime (auto/Python/Node.js/.NET/static/Dockerfile), ZIP or Git source, port, start command,
  health-check path, CPU/RAM limits.
- **Runtime detection** — `Dockerfile` › `*.csproj` (.NET, up to 3 levels deep, SDK version from
  `TargetFramework`) › `package.json` › `requirements.txt`/`pyproject.toml` › `index.html`; the user
  can override it. Start commands are inferred (Procfile, FastAPI/Flask
  apps, `npm start`…) or set explicitly.
- **Builds** — BuildKit (`docker buildx`) on an isolated builder with its own CPU/RAM caps and
  network, build timeout, live output, one image per deployment:
  `mini-paas/project-<id>:deployment-<n>`.
- **Containers** — `paas-project-<project>-deployment-<deployment>`, one per deployment, in a
  per-project network; no published ports; Traefik routes `Host(<slug>.localhost)`.
- **Pipeline** — `QUEUED → BUILDING → DEPLOYING → HEALTH_CHECKING → RUNNING`, failures recorded
  as `BUILD_FAILED`, `DEPLOY_FAILED`, `HEALTH_CHECK_FAILED`, `RUNTIME_CRASHED`, `TIMEOUT` or
  `INTERNAL_ERROR` with the message, last application output and health-check details.
- **Zero-downtime switch** — the new container must pass health checks before traffic moves;
  a failed deployment leaves the previous version serving.
- **Concurrency** — at most one active deployment per project (partial unique index), a
  per-user cap, and Redis locks around container lifecycle operations.
- **Logs** — build/deploy/runtime logs persisted in PostgreSQL in batches (per-deployment byte
  budget, retention) and streamed live; terminal-style viewer with auto-scroll that pauses when
  you scroll up, filters, search, timestamps, colours per level, download.
- **Environment variables** — Fernet-encrypted at rest, secrets masked in every list response,
  explicit audited reveal; `PORT` tells the platform which port the app uses.
- **Metrics** — CPU %, memory vs. limit, network RX/TX, uptime and restart count from
  `docker stats`, pushed live to the dashboard and charted over 15 min–24 h.
- **History and rollback** — every deployment with duration, image, timestamps, errors and
  metadata; roll back to any previous successful deployment whose image still exists.
- **Lifecycle** — restart, stop (container removed, route returns a 503 page), start again from
  the last image, delete (containers, images, files and rows removed in the background).
- **Crash detection** — the agent notices containers that die after Docker's restart policy
  (3 retries) gives up and marks the deployment `RUNTIME_CRASHED`.
- **Cleanup** — hourly: stale workspaces, orphan containers, old images (keeps the newest N
  successful deployments + the current one, so rollbacks keep working), old logs/metrics,
  expired refresh tokens; a reaper fails deployments whose worker died.
- **Observability** — JSON structured logs with request, user, project and deployment ids;
  `X-Request-ID` propagation; audit log (`/api/account/activity`, *Activity* page).
- **Rate limiting** — login (per IP and per account), register, deploy, upload, WS tickets.
- **API docs** — OpenAPI at [`/api/docs`](http://localhost/api/docs) and `/api/redoc`;
  consistent error envelope `{"error": {"code", "message", "details"}}`.
- **Health** — `GET /health` (liveness) and `GET /ready` (PostgreSQL + Redis, plus worker/agent
  heartbeats for information).

## Requirements

| To run the platform        | Version                                                       |
|----------------------------|---------------------------------------------------------------|
| Docker Engine / Desktop    | 24+ with Compose v2.24+ and the buildx plugin (bundled)       |
| Free host port             | 80 (configurable with `HTTP_PORT`)                            |
| Browser                    | any modern browser (`*.localhost` resolves to 127.0.0.1)      |

| To develop without Docker  | Version                                                       |
|----------------------------|---------------------------------------------------------------|
| Python                     | 3.12+                                                         |
| Node.js                    | 22+                                                           |
| PostgreSQL / Redis         | 15+ / 7+ (or any Redis-compatible server)                     |

## Installation

```bash
git clone <repository-url> mini-paas
cd mini-paas
cp .env.example .env          # optional overrides only, no secrets needed
docker compose up --build     # or: make up
```

Open **http://localhost**, create an account, then **New Project** → upload
`examples/static` zipped (or any of the examples) → watch it build in the Logs tab → open
`http://<slug>.localhost`.

The first start generates per-installation secrets (JWT key, encryption key, database
password, internal token) into the `paas-secrets` volume. Demo data:

```bash
make seed     # creates demo@minipaas.local (prints a random password) + a demo project
```

## Environment variables

`.env` is optional. Everything has a safe default; generated secrets are used unless you set
them. Any setting from `backend/app/core/config.py` can be overridden the same way.

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` refuses to start with insecure secrets |
| `HTTP_PORT` | `80` | Host port of the proxy |
| `PLATFORM_HOST` | `localhost` | Host serving the dashboard and API |
| `APPS_BASE_DOMAIN` | `localhost` | Apps are served at `<slug>.<APPS_BASE_DOMAIN>` |
| `PUBLIC_SCHEME` / `PUBLIC_PORT` | `http` / – | Used to build app URLs |
| `COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `POSTGRES_USER` / `POSTGRES_DB` | `minipaas` | Database identity (password is generated) |
| `DATABASE_URL` | – | Full URL instead of the `POSTGRES_*` parts |
| `REDIS_URL` | `redis://redis:6379/0` | Broker, pub/sub and rate limits |
| `JWT_SECRET` | generated | HS256 signing key (≥ 32 chars in production) |
| `ENCRYPTION_KEYS` | generated | Comma-separated Fernet keys; first encrypts, all decrypt |
| `INTERNAL_API_TOKEN` | generated | Authenticates Traefik's config polling |
| `DOCKER_HOST` | socket | Docker endpoint for the worker and agent |
| `MAX_PROJECTS_PER_USER` | `10` | Quota |
| `MAX_ACTIVE_DEPLOYMENTS_PER_USER` | `2` | Concurrent pipelines per user (1 per project is fixed) |
| `MAX_UPLOAD_MB` / `MAX_EXTRACTED_MB` | `100` / `500` | Archive limits |
| `APP_CPU_DEFAULT` / `APP_CPU_MAX` | `0.5` / `1.0` | CPUs per app |
| `APP_MEMORY_DEFAULT_MB` / `APP_MEMORY_MAX_MB` | `256` / `512` | RAM per app (no swap) |
| `APP_PIDS_LIMIT` | `256` | Processes per app |
| `BUILD_TIMEOUT_SECONDS` | `900` | Build timeout |
| `BUILD_CPU_LIMIT` / `BUILD_MEMORY_MB` | `1.0` / `1024` | Isolated builder caps |
| `HEALTH_CHECK_TIMEOUT_SECONDS` | `60` | Time for a new container to answer |
| `MAX_LOG_BYTES_PER_DEPLOYMENT` | `10485760` | Log budget per deployment |
| `LOG_RETENTION_DAYS` / `METRICS_RETENTION_HOURS` | `14` / `24` | Retention |
| `IMAGE_RETENTION_PER_PROJECT` | `3` | Successful images kept for rollback |
| `RATE_LIMIT_LOGIN` … `_REGISTER`, `_DEPLOY`, `_UPLOAD`, `_WS_TICKET` | see config | `requests/seconds` |
| `SEED_DEMO_PASSWORD` | random | Password used by `make seed` |

## Docker services

| Service | Role | Docker socket |
|---|---|---|
| `proxy` | Traefik v3 edge router (port 80) | no |
| `frontend` | nginx (unprivileged) serving the SPA and the 503 page | no |
| `backend` | FastAPI: REST, WebSockets, Traefik config endpoint | no |
| `migrate` | one-shot `alembic upgrade head` | no |
| `secrets-init` | one-shot secret generation | no (no network) |
| `worker` | Celery `deployments` queue: builds and deploys | yes |
| `ops-worker` | Celery `ops`, `maintenance`: stop/restart/delete, cleanup | yes |
| `scheduler` | Celery beat: cleanup, reaper, heartbeats | no |
| `agent` | runtime logs, `docker stats`, crash detection, proxy reconciliation | yes |
| `postgres`, `redis` | data stores on an `internal` network with no internet route | no |

Platform containers run with `no-new-privileges`, most with a read-only root filesystem and all
capabilities dropped. Logs are size-capped (`json-file`, 10 MB × 3).

## Database and migrations

Tables: `users`, `refresh_tokens`, `projects`, `deployments`, `environment_variables`,
`deployment_logs`, `project_metrics`, `audit_logs` — UUID keys, foreign keys with explicit
`ON DELETE`, CHECK constraints for enums and ranges, and indexes for every hot query
(including the partial unique index that guarantees one active deployment per project).

```bash
make migrate                                                   # apply (also automatic on `up`)
cd backend && .venv/bin/alembic revision --autogenerate -m "…" # new migration
cd backend && .venv/bin/alembic check                          # models and migrations in sync
```

## Development

Backend:

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cat > .env <<'EOF'
DATABASE_URL=postgresql+asyncpg://minipaas:minipaas@127.0.0.1:5432/minipaas
REDIS_URL=redis://127.0.0.1:6379/0
JWT_SECRET=dev-only-secret-change-me-0123456789abcdef
ENCRYPTION_KEYS=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
INTERNAL_API_TOKEN=dev-internal-token
DATA_DIR=./.data
LOG_JSON=false
EOF
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/celery -A app.workers.celery_app worker -Q deployments,ops,maintenance   # -P solo on Windows
.venv/bin/python -m app.agent                                                      # needs Docker
```

Frontend (Vite proxies `/api` and `/ws` to `127.0.0.1:8000`):

```bash
cd frontend && npm ci && npm run dev    # http://localhost:5173
```

Without a Docker daemon everything works up to the build step, which then fails cleanly with
`INTERNAL_ERROR: Docker is unavailable`, so the dashboard, auth, uploads, detection, logs and
history can still be developed. The worker must run inside Docker for health checks to reach
app networks.

Useful commands: `make help`, `make lint`, `make format`, `make test`, `make logs`.

## Testing

| Suite | Command | Needs |
|---|---|---|
| Backend unit (state machine, archives, git/SSRF, runtime detection, Dockerfiles, DockerService with fakes, JWT, crypto, health checks…) | `cd backend && .venv/bin/pytest tests/unit` | nothing |
| Backend integration (API + PostgreSQL + Redis: auth, permissions, projects, deployments, concurrency, env, logs, metrics, WebSockets, worker pipeline, lifecycle, cleanup, agent) | `cd backend && .venv/bin/pytest tests/integration` | `TEST_DATABASE_URL`, `TEST_REDIS_URL` |
| Frontend unit (format, validation, stores, API client refresh, LogViewer, cards) | `cd frontend && npm test` | nothing |
| E2E (register, login, create project, upload, live logs, history, env, responsive) | `cd e2e && E2E_BASE_URL=http://localhost npx playwright test` | running stack |
| E2E full deploy (deploy → live URL → metrics → restart → stop → 503 → start) | add `E2E_DOCKER=1` | stack with Docker |

Integration tests create the schema with the real Alembic migrations and truncate tables
between tests. The worker pipeline is exercised against the real database with an in-memory
Docker double (`tests/fakes.py`); the real Docker path is covered by the CI E2E job.

CI (`.github/workflows/ci.yml`): lint → type check → unit → integration → migration drift →
frontend lint/types/tests/build → `docker compose build` → `docker compose up` → smoke tests →
full E2E with real deployments → seed. Nothing is deployed anywhere.

## Security

The platform runs code it did not write. The controls, and what remains:

**User code at runtime** — one container per deployment: `cap_drop: ALL`,
`no-new-privileges`, generated images run as a non-root user with application files owned by
root and a **read-only root filesystem** (`/tmp` is a `noexec` tmpfs), memory limit with no
swap, CPU quota, PID limit, `nofile` ulimit, restart policy capped at 3, size-capped logs, no
published ports, no host mounts, no Docker socket, platform secrets never injected. Each
project has its own bridge network: apps cannot reach each other, PostgreSQL, Redis or the API.

**User code at build time** — BuildKit runs Dockerfile steps in unprivileged sandboxes on an
isolated builder container with CPU/RAM caps and its own network; builds are time-limited and
receive no secrets. Build-time environment variables are intentionally not supported.

**Untrusted input** — ZIPs are checked for size, entry count, compression ratio, absolute
paths, `..`, symlinks and encryption, and extraction counts real bytes. Git URLs must be public
`https://`, without credentials; every resolved address must be public and the clone pins the
validated IP (no DNS rebinding), with redirects, hooks, submodules, LFS, credential helpers and
symlinks disabled. Commands are always argument lists (no shell). Start commands are
JSON-encoded into `CMD`. Slugs, branches, env keys and health paths are validated.

**Accounts and API** — Argon2id; constant-time login (dummy hash) with identical errors;
password policy; JWT `HS256` pinned with `iss`/`aud`/`typ`/`exp`; refresh token rotation with
family revocation on reuse; httpOnly `SameSite=Strict` cookie scoped to `/api/auth`; WebSocket
tickets instead of JWTs in URLs; ownership enforced in every repository query (404 for others'
resources); rate limits; consistent errors without stack traces; security headers and CSP.

**Secrets** — environment values are Fernet-encrypted with rotatable keys, masked in lists,
revealed only by an audited POST and never logged or audited by value. Platform secrets are
generated per installation, stored in a volume readable only by platform containers, and
`ENVIRONMENT=production` refuses to start with weak values.

**Residual risks (documented, by design of this MVP)**

1. **The Docker socket** is mounted into `worker`, `ops-worker` and `agent`; it is
   root-equivalent on the host. Those services are not internet-facing and never run user code,
   but a vulnerability in them would compromise the host. Mitigations to adopt in production:
   a socket proxy with an allow-list, rootless Docker, or a remote builder/runtime.
2. **Shared kernel** — containers are not VMs. For hostile multi-tenant workloads use
   `userns-remap`, gVisor/Kata, or one VM per tenant.
3. **Egress** — apps and builds have outbound internet. On cloud hosts block the metadata
   service and private ranges with [`infrastructure/scripts/harden-host.sh`](infrastructure/scripts/harden-host.sh).
4. **Dockerfile runtime** images may run as root inside the container (capabilities are still
   dropped) and keep a writable root filesystem. **.NET** images run as a non-root user but keep a
   writable filesystem, because ASP.NET apps commonly store SQLite files and uploads in `App_Data`
   (ephemeral: lost on every redeploy).
5. **Disk usage** of a container's writable layer cannot be capped on overlay2 without xfs
   quotas.
6. **Redis** has no password; it is reachable only on the `internal` network.
7. **HTTP only** — enable TLS at the proxy before exposing the platform (see below).

## Deploying to a server

1. A Linux host with Docker, a wildcard DNS record `*.apps.example.com` and `paas.example.com`.
2. `.env`: `ENVIRONMENT=production`, `PLATFORM_HOST=paas.example.com`,
   `APPS_BASE_DOMAIN=apps.example.com`, `PUBLIC_SCHEME=https`, `COOKIE_SECURE=true`.
3. Add TLS to Traefik (an `websecure` entry point with a Let's Encrypt resolver; a DNS challenge
   is needed for the wildcard) and redirect HTTP → HTTPS.
4. Run `infrastructure/scripts/harden-host.sh`; back up the `postgres-data` and `paas-secrets`
   volumes (losing `paas-secrets` makes stored environment variables unreadable).
5. For more than ~25 concurrently running projects, widen Docker's `default-address-pools` in
   `/etc/docker/daemon.json`, e.g. `{"default-address-pools":[{"base":"10.200.0.0/16","size":26}]}`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `http://localhost` does not load | Port 80 busy: set `HTTP_PORT=8080` and `PUBLIC_PORT=8080`, then `docker compose up -d` |
| Deployment fails with `Docker is unavailable` | The worker cannot reach the daemon: check the socket mount / `DOCKER_HOST` |
| Build fails pulling base images | No internet from the builder, or Docker Hub rate limits: retry or log in on the host |
| `HEALTH_CHECK_FAILED` | The app must listen on `0.0.0.0:$PORT`; set `PORT` or the project port, or the health-check path, and read “Last application output” in the logs |
| `Could not detect the runtime` | Put `Dockerfile`, a `.csproj`, `package.json`, `requirements.txt`/`pyproject.toml` or `index.html` at the archive root (or in one top-level folder). Other stacks (Go, Java, PHP…) need a `Dockerfile` |
| .NET app exits at startup | Apps that create their database on start usually need a setting such as `Database__ApplyMigrationsOnStartup=true` in the Environment tab; `__` maps to `:` in ASP.NET configuration |
| App URL shows the 503 page | The project is stopped, crashed or still deploying |
| `429 Too many requests` | Rate limits: wait `Retry-After` seconds or raise `RATE_LIMIT_*` |
| `pull access denied for mini-paas-backend` | Use `docker compose up --build` (images are built locally) |
| Builds hang at “creating builder” | First build bootstraps the isolated BuildKit builder; check `docker buildx ls` |
| Reset everything | `make clean` (deletes all data volumes, asks first) |

Logs: `docker compose logs -f backend worker agent`. Every API log line carries `request_id`;
worker lines carry `deployment_id` and `project_id`.

## Known limitations

- Public Git repositories only (no tokens/SSH keys yet); one branch per project.
- No build-time environment variables or build cache tuning; no monorepo sub-directories.
- One container per project (no horizontal scaling); no persistent volumes — data must live
  in an external database.
- HTTP only out of the box; custom domains are designed for (`DomainService.hosts_for`) but
  not exposed in the UI.
- Health checks are HTTP only; apps that do not serve HTTP cannot pass them.
- Metrics are sampled every 10 s and kept 24 h; logs are kept 14 days in PostgreSQL.
- Single-node Docker; the worker and agent assume they run on the Docker host.

## Roadmap

GitHub/GitLab integration and webhooks · custom domains and automatic HTTPS · managed
PostgreSQL/Redis per project · persistent volumes · cron jobs and background workers ·
horizontal scaling and autoscaling · Kubernetes / Docker Swarm backend · cloud providers ·
teams, organizations and RBAC · usage limits and billing · S3-compatible storage for
archives · Prometheus/Grafana metrics · Loki for logs (the `LogSink` is the seam) ·
OpenTelemetry tracing.
