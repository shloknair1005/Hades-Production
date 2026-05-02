# Judges of Hades 🔱 — Production Backend

A multi-agent Decision Support System. Submit a business problem as free text; three AI strategists (Sales, Operations, Finance) independently analyse it, then a fourth agent — **Hades** — synthesises a final decision. Every run is versioned, shareable, and commentable. Analytics track which agent your team trusts most over time.

---

## Table of Contents

1. [What Changed from the Original](#what-changed)
2. [Architecture Overview](#architecture-overview)
3. [File Structure](#file-structure)
4. [Prerequisites](#prerequisites)
5. [Environment Setup](#environment-setup)
6. [Database Setup](#database-setup)
7. [Running the Server](#running-the-server)
8. [How the Decision Pipeline Works](#how-the-decision-pipeline-works)
9. [API Reference](#api-reference)
10. [Agent Configuration](#agent-configuration)
11. [Auth Flow](#auth-flow)
12. [Analytics](#analytics)
13. [What Is Not Yet Built](#what-is-not-yet-built)

---

## What Changed

The original `Judges-of-Hades-Ollama` repo was a minimal Flask/FastAPI app with a single `/decide` endpoint, hardcoded Ollama calls, and mock in-memory storage. This rebuild is the production-grade replacement:

| Area | Before | Now |
|---|---|---|
| Persistence | `mock_memory.py` dict | PostgreSQL (10 tables, async SQLAlchemy 2.0) |
| Auth | None | Custom JWT (access + refresh tokens, bcrypt, server-side revocation) |
| Decision history | Single response | Full run versioning — trigger the same problem 10 times, compare side by side |
| Agent config | Hardcoded prompts | DB-driven per-org config with version history and live activation |
| Background execution | Synchronous, blocking | Async via `asyncio.create_task`, HTTP 202 + poll pattern |
| Social | None | Comments (threaded), feedback (per-agent rating), sharing (public or org-scoped links) |
| Analytics | None | Personal, org, and agent-trust dashboards; append-only event log |
| Multi-tenancy | None | Organisation model; every user belongs to an org; visibility scoping |

---

## Architecture Overview

```
Browser / API client
        │
        ▼
  FastAPI (main.py)
  ├── auth_router          POST /auth/*
  ├── problems_router      CRUD /problems
  ├── runs_router          GET  /runs/:id, /runs/:id/agents
  ├── feedback_router      POST/GET /runs/:id/feedback
  ├── comments_router      CRUD /runs/:id/comments, /comments/:id
  ├── shares_router        POST/GET/DELETE /runs/:id/share, GET /shared/:token
  ├── analytics_router     GET /analytics/me, /analytics/org, /analytics/org/agents
  └── admin_router         CRUD /admin/users, /admin/orgs, /admin/agent-configs

  Services
  ├── auth_service.py      register, login, refresh, logout
  └── run_service.py       problem access, run creation, async pipeline, prompt builders

  Background task (_execute_run)
  ├── sales agent    ──► Ollama
  ├── ops agent      ──► Ollama
  ├── finance agent  ──► Ollama
  └── hades agent    ──► Ollama (reads all three outputs)

  PostgreSQL (10 tables)
  ├── Identity:   organizations, users, refresh_tokens
  ├── Decisions:  problems, decision_runs, agent_outputs
  ├── Config:     agent_configs
  ├── Social:     decision_shares, feedback, comments
  └── Analytics:  usage_events, agent_trust_scores
```

---

## File Structure

This is the **target** layout for the new backend. All files go inside a project root (e.g. `judges-of-hades/`).

```
judges-of-hades/
│
├── api/
│   ├── __init__.py
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py          ← settings from .env (DB URL, JWT secret, Ollama host/model)
│   │   ├── database.py        ← async engine, AsyncSessionLocal, Base, get_db dependency
│   │   └── jwt.py             ← create_access_token, create_refresh_token, decode_token
│   │
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── auth.py            ← get_current_user, require_admin, require_power_or_admin
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── orm.py             ← all 10 SQLAlchemy ORM models
│   │   └── schemas.py         ← all Pydantic request/response schemas
│   │
│   ├── routers/
│   │   ├── __init__.py
│   │   └── all_routers.py     ← all 8 APIRouter instances (auth, problems, runs, feedback,
│   │                             comments, shares, analytics, admin)
│   │
│   └── services/
│       ├── __init__.py
│       ├── auth_service.py    ← register, login, refresh, logout, _issue_tokens
│       └── run_service.py     ← problem/run CRUD, async pipeline, prompt builders
│
├── static/
│   └── index.html             ← frontend UI (served at GET /)
│
├── main.py                    ← FastAPI app factory; registers all routers; lifespan hook
├── requirements.txt
└── .env                       ← secrets (never commit)
```

### Creating the `__init__.py` files

Every directory under `api/` needs an empty `__init__.py` so Python treats it as a package:

```bash
touch api/__init__.py \
      api/core/__init__.py \
      api/middleware/__init__.py \
      api/models/__init__.py \
      api/routers/__init__.py \
      api/services/__init__.py
```

### Placing each uploaded file

| File you have | Where it goes |
|---|---|
| `main.py` | `main.py` (project root) |
| `config.py` | `api/core/config.py` |
| `database.py` | `api/core/database.py` |
| `jwt.py` | `api/core/jwt.py` |
| `auth.py` | `api/middleware/auth.py` |
| `orm.py` | `api/models/orm.py` |
| `schemas.py` | `api/models/schemas.py` |
| `all_routers.py` | `api/routers/all_routers.py` |
| `auth_service.py` | `api/services/auth_service.py` |
| `run_service.py` | `api/services/run_service.py` |
| `requirements.txt` | `requirements.txt` (project root) |

Copy the `static/` folder from the original `Judges-of-Hades-Ollama/static/` as-is.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.10 minimum; 3.11+ recommended |
| PostgreSQL | 14+ | Must be running before the app starts |
| Ollama | latest | Must be running with at least one model pulled |
| pip | — | Comes with Python |

### Install Ollama and pull a model

```bash
# macOS / Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Pull the default model (mistral) or any model you prefer
ollama pull mistral
```

The model name must match `OLLAMA_MODEL` in your `.env`.

---

## Environment Setup

### 1. Create and activate a virtual environment

```bash
# From your project root
python -m venv .venv

# Activate (macOS / Linux)
source .venv/bin/activate

# Activate (Windows)
.venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` contents:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0
pydantic[email]>=2.7.0
pyjwt>=2.8.0
bcrypt>=4.1.0
httpx>=0.27.0
python-dotenv>=1.0.0
langgraph>=0.0.55
```

### 3. Create `.env`

Create a `.env` file in the project root. **Never commit this file.**

```dotenv
# Database — asyncpg driver required
DATABASE_URL=postgresql+asyncpg://youruser:yourpassword@localhost:5432/hades

# JWT — change this to a long random string in production
JWT_SECRET=replace-this-with-a-real-secret-at-least-32-chars

# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral
```

Generate a strong `JWT_SECRET`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Database Setup

### 1. Create the PostgreSQL database

```bash
# Connect as your postgres superuser
psql -U postgres

# Inside psql
CREATE DATABASE hades;
CREATE USER youruser WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE hades TO youruser;
\q
```

### 2. Tables are created automatically on startup

The `lifespan` function in `main.py` calls `Base.metadata.create_all` when the server starts. **You do not need to run any migrations** for an initial setup — start the server and all 10 tables will be created automatically.

```python
# main.py (lifespan hook — runs on startup)
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
```

> **Note:** `create_all` is additive — it creates missing tables but never drops or alters existing ones. For schema changes after initial setup, use Alembic or manual `ALTER TABLE` statements.

---

## Running the Server

### Development (auto-reload)

Run from the **project root** (the directory that contains `main.py`):

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- `main:app` — the `app` object inside `main.py`
- `--reload` — restarts on code changes
- `--host 0.0.0.0` — accessible on your local network, not just localhost

### Production

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

Do not use `--reload` in production. `--workers 4` starts 4 Uvicorn processes (use `2 × CPU cores + 1` as a rule of thumb).

### Verify it is running

Open `http://localhost:8000` — the frontend `index.html` is served there.

Visit `http://localhost:8000/docs` for the interactive Swagger UI with all endpoints.

### Common startup errors

| Error | Fix |
|---|---|
| `could not connect to server` | PostgreSQL is not running. Start it with `pg_ctl start` or `brew services start postgresql`. |
| `password authentication failed` | Wrong credentials in `DATABASE_URL` in `.env`. |
| `ModuleNotFoundError: No module named 'api'` | You are not running from the project root, or `__init__.py` files are missing. |
| `httpx.ConnectError` (during a run) | Ollama is not running. Start it with `ollama serve`. |

---

## How the Decision Pipeline Works

### Step 1 — Create a problem (no AI yet)

```
POST /problems
Body: { "title": "Should we expand to Europe in Q3?", "visibility": "private" }
```

Returns a `problem_id`. A Problem is a logical container — the question itself.

### Step 2 — Trigger a run (AI starts here)

```
POST /problems/{problem_id}/runs
Body: { "problem_text": "We have 12 months of runway, 3 enterprise clients in Germany..." }
```

Returns HTTP **202** immediately with a `run_id` and `status: "pending"`. The pipeline runs in the background.

### Step 3 — Poll for completion

```
GET /runs/{run_id}
```

Repeat until `status` is `"done"` or `"failed"`. The `final_decision` field is populated on `"done"`.

### Step 4 — Read individual agent outputs

```
GET /runs/{run_id}/agents
```

Returns the Sales, Operations, Finance, and Hades outputs — each with the exact prompt used, latency in milliseconds, and token count.

### Background pipeline sequence

```
_execute_run (background task)
  1. Load active AgentConfig rows for this org (or fall back to defaults)
  2. sales agent   → call Ollama → write AgentOutput row
  3. ops agent     → call Ollama → write AgentOutput row
  4. finance agent → call Ollama → write AgentOutput row
  5. hades agent   → call Ollama with all three outputs → write AgentOutput row
  6. Set run.final_decision, run.status = "done", run.completed_at
```

If any Ollama call fails, the entire run is marked `"failed"`.

### Run versioning

Triggering the same problem multiple times creates numbered versions (v1, v2, v3…). List all versions:

```
GET /problems/{problem_id}/runs
```

---

## API Reference

All endpoints require a Bearer token in the `Authorization` header unless stated otherwise.

```
Authorization: Bearer <access_token>
```

### Auth

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/register` | None | Create user + org atomically. Returns token pair. |
| POST | `/auth/login` | None | Verify password. Returns token pair. |
| POST | `/auth/refresh` | None | Rotate refresh token. Send `{ "refresh_token": "..." }`. |
| POST | `/auth/logout` | Bearer | Revoke refresh token. Send `{ "refresh_token": "..." }`. |
| GET | `/auth/me` | Bearer | Returns current user profile. |

### Problems

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/problems` | Bearer | List your problems. Supports `?visibility=private\|org\|public&page=1&page_size=20`. |
| POST | `/problems` | Bearer | Create a problem. Body: `{ "title": "...", "visibility": "private" }`. |
| GET | `/problems/{id}` | Bearer | Get one problem. |
| PATCH | `/problems/{id}` | Bearer | Update title or visibility (owner only). |
| DELETE | `/problems/{id}` | Bearer | Soft-delete (owner only). Sets `is_deleted = true`. |

### Runs

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/problems/{id}/runs` | Bearer | Trigger a run. Body: `{ "problem_text": "..." }`. Returns 202. |
| GET | `/problems/{id}/runs` | Bearer | List all run versions for a problem. |
| GET | `/runs/{run_id}` | Bearer | Get run status and final decision. Poll until done/failed. |
| GET | `/runs/{run_id}/agents` | Bearer | Get all four agent outputs for a run. |

### Feedback

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/runs/{run_id}/feedback` | Bearer | Submit feedback. Body: `{ "chosen_agent": "sales", "rating": 4, "notes": "..." }`. One per user per run. |
| GET | `/runs/{run_id}/feedback` | Bearer | Get your feedback for a run. |

### Comments

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/runs/{run_id}/comments` | Bearer | List all comments (ordered by time). |
| POST | `/runs/{run_id}/comments` | Bearer | Post a comment. Body: `{ "body": "...", "parent_id": null }`. Use `parent_id` for replies. |
| PATCH | `/comments/{comment_id}` | Bearer | Edit your comment. Body: `{ "body": "..." }`. |
| DELETE | `/comments/{comment_id}` | Bearer | Delete your comment (or any comment if admin). |

### Sharing

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/runs/{run_id}/share` | Bearer | Create a share. Body: `{ "is_public_link": false, "expires_at": null }`. |
| GET | `/runs/{run_id}/share` | Bearer | Get the active share for a run. |
| DELETE | `/runs/{run_id}/share` | Bearer | Revoke the share (deletes the token immediately). |
| GET | `/shared/{share_token}` | **None** | Public endpoint — view a shared run by token. Returns 410 if expired. |

### Analytics

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/analytics/me` | Bearer | Your personal stats: total runs, avg rating given, most trusted agent, runs this month. |
| GET | `/analytics/org` | Bearer | Org-wide stats: total runs, active users, most trusted agent, avg rating. |
| GET | `/analytics/org/agents` | Bearer | Agent trust trend from the pre-aggregated `agent_trust_scores` table. |

### Admin

All admin routes require `role = admin`. Agent config routes require `role = admin` or `is_power_user = true`.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/admin/users` | Admin | List all users. Paginated. |
| PATCH | `/admin/users/{id}` | Admin | Change user role, power_user flag, or org. |
| GET | `/admin/orgs` | Admin | List all organisations. |
| PATCH | `/admin/orgs/{id}` | Admin | Update org name or plan. |
| GET | `/admin/agent-configs` | Power/Admin | List agent configs for your org. |
| POST | `/admin/agent-configs` | Power/Admin | Create a new config version. |
| PATCH | `/admin/agent-configs/{id}/activate` | Power/Admin | Activate a config version (deactivates previous). |
| GET | `/admin/agent-configs/{id}/runs` | Power/Admin | List runs that used a specific config version. |
| GET | `/admin/analytics/platform` | Admin | Platform-wide counts: total runs, users, orgs. |
| GET | `/admin/analytics/usage-events` | Admin | Raw usage event log. Filterable by event_type and org_id. |

---

## Agent Configuration

By default the system runs with the hardcoded prompts in `run_service.py` — you do not need to configure anything to get started.

To customise an agent for your org:

```
# 1. Create a new config version (is_active defaults to false)
POST /admin/agent-configs
{
  "agent_name": "sales",
  "model": "mistral",
  "system_prompt": "You are a B2B SaaS sales strategist...",
  "temperature": 0.6,
  "max_tokens": 600
}

# 2. Activate it (deactivates any previous active version for this agent)
PATCH /admin/agent-configs/{config_id}/activate
```

Valid `agent_name` values: `sales`, `operations`, `finance`, `hades`.

Config is loaded fresh for every run — activating a new version takes effect on the next triggered run with no restart required.

---

## Auth Flow

### Registration (first user of an org)

The first user who registers an org is automatically granted `role = admin` and `is_power_user = true`.

```
POST /auth/register
{
  "email": "you@example.com",
  "password": "secure-password",
  "display_name": "Your Name",
  "org_name": "Acme Corp",
  "org_slug": "acme"       ← must be unique across the platform
}
```

### Token lifecycle

- **Access token**: valid for 30 minutes. Send as `Authorization: Bearer <token>`.
- **Refresh token**: valid for 30 days. Send to `POST /auth/refresh` to get a new pair.
- **Rotation**: every refresh call revokes the old refresh token and issues a new one (one-time use).
- **Logout**: sends the refresh token to `POST /auth/logout`, which revokes it server-side. The access token remains valid until it naturally expires (no server-side access token revocation).
- **Storage**: refresh tokens are stored as SHA-256 hashes in the database — the raw token is never persisted.

---

## Analytics

### Personal analytics (`GET /analytics/me`)

Returns stats scoped to the current user: total runs triggered, average rating they gave, their most-trusted agent, and how many runs they triggered this calendar month.

### Org analytics (`GET /analytics/org`)

Returns stats scoped to the current user's organisation: total runs, distinct active users, org-wide average rating, and the most trusted agent from the pre-aggregated trust score table.

### Agent trust trends (`GET /analytics/org/agents`)

Reads from the `agent_trust_scores` table — a pre-aggregated table designed to be populated by a nightly scheduled job (see "What Is Not Yet Built" below). Until the job runs, this endpoint returns an empty list.

---

## What Is Not Yet Built

These three items are the documented next steps after the backend:

**1. `agent_trust_scores` aggregation job**
A scheduled task (cron, Celery beat, or a Postgres `pg_cron` job) that rolls up the `feedback` table nightly into `agent_trust_scores`. Without it, `GET /analytics/org/agents` always returns `[]`.

A minimal version would look like:
```sql
INSERT INTO agent_trust_scores (agent_name, org_id, period, times_chosen, total_runs, avg_rating)
SELECT
  f.chosen_agent,
  p.org_id,
  DATE_TRUNC('month', f.created_at)::date,
  COUNT(*) FILTER (WHERE f.chosen_agent IS NOT NULL),
  COUNT(DISTINCT dr.id),
  AVG(f.rating)
FROM feedback f
JOIN decision_runs dr ON f.run_id = dr.id
JOIN problems p ON dr.problem_id = p.id
GROUP BY f.chosen_agent, p.org_id, DATE_TRUNC('month', f.created_at)::date
ON CONFLICT DO UPDATE SET ...;
```

**2. Middleware layer**
Rate limiting, structured request logging (correlation IDs), and standardised error response shapes are not yet applied. The brief specifies this as the next backend layer.

**3. Frontend rebuild**
The `static/index.html` from the original repo works with the original `/decide` endpoint. It needs to be rebuilt to use the new versioned API (auth, problems, runs, polling, comments, sharing, analytics dashboards).
