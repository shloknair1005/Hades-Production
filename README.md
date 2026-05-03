<div align="center">

<h1>⚖️ Judges of Hades</h1>

<p><strong>Decision Intelligence System — Production Backend</strong></p>

<p><em>Three AI strategists. One arbiter. Every business decision, structurally analysed.</em></p>

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-336791?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?style=flat-square&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Ollama](https://img.shields.io/badge/Ollama-Compatible-000000?style=flat-square&logo=ollama&logoColor=white)](https://ollama.ai/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Production--Ready-brightgreen?style=flat-square)]()

</div>

---

## Overview

**Judges of Hades** is a production-grade multi-agent decision support system. Submit any business problem as free text; three specialist AI agents — Sales, Operations, and Finance — independently analyse it in parallel, then a fourth agent, **Hades**, synthesises a final, balanced verdict.

Every decision is versioned, shareable, and commentable. An analytics layer tracks which agent your team trusts most over time, enabling continuous improvement of the decision pipeline.

This repository is the **production backend rebuild** of the original `Judges-of-Hades-Ollama` prototype.

---

## What's New in This Version

The original prototype used a minimal Flask/FastAPI app with a single `/decide` endpoint, hardcoded Ollama calls, and mock in-memory storage. This is the production replacement:

| Area | Before | Now |
|---|---|---|
| **Persistence** | `mock_memory.py` dict | PostgreSQL (10 tables, async SQLAlchemy 2.0) |
| **Authentication** | None | Custom JWT (access + refresh tokens, bcrypt, server-side revocation) |
| **Decision History** | Single response | Full run versioning — trigger the same problem 10× and compare |
| **Agent Configuration** | Hardcoded prompts | DB-driven per-org config with version history and live activation |
| **Background Execution** | Synchronous, blocking | Async via `asyncio.create_task`, HTTP 202 + poll pattern |
| **Collaboration** | None | Threaded comments, per-agent feedback & ratings, shareable links |
| **Analytics** | None | Personal, org-level, and agent-trust dashboards |
| **Multi-tenancy** | None | Organisation model; visibility scoping (private / org / public) |
| **Middleware** | None | Rate limiting, request logging, payload guard, error handler, cache |

---

## Architecture

```
Browser / API Client
        │
        ▼
  FastAPI Application  (async, Python 3.11+)
  ├── auth_router          POST /auth/*
  ├── problems_router      CRUD /problems
  ├── runs_router          GET  /runs/:id, /runs/:id/agents
  ├── feedback_router      POST/GET /runs/:id/feedback
  ├── comments_router      CRUD /runs/:id/comments, /comments/:id
  ├── shares_router        POST/GET/DELETE /runs/:id/share, GET /shared/:token
  ├── analytics_router     GET /analytics/me, /analytics/org, /analytics/org/agents
  └── admin_router         CRUD /admin/users, /admin/orgs, /admin/agent-configs

  Middleware Stack
  ├── JWT Authentication + Role Enforcement
  ├── Rate Limiter         (per-user, per-org, per-IP sliding windows)
  ├── Payload Guard        (size + schema validation)
  ├── Request Logger       (correlation IDs, error tracking, auto-purge)
  ├── Cache Layer          (5-min TTL in-process agent config cache)
  └── Error Handler        (standardised JSON error shapes)

  Background Decision Pipeline  (asyncio.create_task → HTTP 202)
  ├── sales agent    ──► Ollama LLM
  ├── ops agent      ──► Ollama LLM
  ├── finance agent  ──► Ollama LLM
  └── hades agent    ──► Ollama LLM (reads all three, synthesises verdict)

  PostgreSQL — 10 Tables
  ├── Identity:   organizations, users, refresh_tokens
  ├── Decisions:  problems, decision_runs, agent_outputs
  ├── Config:     agent_configs
  ├── Social:     decision_shares, feedback, comments
  └── Analytics:  usage_events, agent_trust_scores

  APScheduler  (AsyncIOScheduler, UTC)
  ├── Nightly:  agent_trust_scores aggregation
  ├── Daily:    rate_limit_windows cleanup
  └── Monthly:  request_log purge (30d standard / 60d error)
```

---

## Project Structure

```
judges-of-hades/
│
├── api/
│   ├── core/
│   │   ├── config.py          ← Settings loaded from .env
│   │   ├── database.py        ← Async engine, AsyncSessionLocal, Base, get_db
│   │   ├── jwt.py             ← Token creation and decoding
│   │   └── scheduler.py       ← APScheduler background jobs
│   │
│   ├── middleware/
│   │   ├── auth.py            ← get_current_user, require_admin, require_power_or_admin
│   │   ├── cache.py           ← In-process TTL cache for agent configs + analytics
│   │   ├── error_handler.py   ← Standardised error response shapes
│   │   ├── payload_guard.py   ← Request size + schema validation
│   │   ├── rate_limiter.py    ← Sliding window rate limits (user / org / IP)
│   │   └── request_logger.py  ← Correlation IDs + structured logging
│   │
│   ├── models/
│   │   ├── orm.py             ← All 10 SQLAlchemy ORM models
│   │   └── schemas.py         ← All Pydantic request/response schemas
│   │
│   ├── routers/
│   │   └── all_routers.py     ← All 8 APIRouter instances
│   │
│   └── services/
│       ├── auth_service.py    ← register, login, refresh, logout, _issue_tokens
│       └── run_service.py     ← Problem/run CRUD, async decision pipeline, prompt builders
│
├── static/
│   └── index.html             ← Frontend UI (served at GET /)
│
├── main.py                    ← FastAPI app factory; registers routers; lifespan hook
├── requirements.txt
└── .env                       ← Secrets — never commit
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.10 minimum; 3.11+ recommended |
| PostgreSQL | 14+ | Must be running before the server starts |
| Ollama | latest | Must be running with at least one model pulled |

### Install Ollama

```bash
# macOS / Linux
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull mistral
```

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/your-org/judges-of-hades.git
cd judges-of-hades

python -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

**`requirements.txt`**
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
apscheduler>=3.10.0
```

### 3. Configure environment variables

Create a `.env` file in the project root. **Never commit this file.**

```dotenv
# Database — asyncpg driver required
DATABASE_URL=postgresql+asyncpg://youruser:yourpassword@localhost:5432/hades

# JWT — must be a long random string
JWT_SECRET=replace-this-with-a-real-secret-at-least-32-chars

# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral
```

Generate a secure `JWT_SECRET`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Create the database

```bash
psql -U postgres
```

```sql
CREATE DATABASE hades;
CREATE USER youruser WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE hades TO youruser;
\q
```

Tables are created automatically on first server start via the `lifespan` hook — no migrations needed for initial setup.

---

## Running the Server

### Development

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Production

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

Use `2 × CPU cores + 1` as a guide for `--workers`. Do not use `--reload` in production.

Once running:
- **Frontend:** `http://localhost:8000`
- **Interactive API docs (Swagger):** `http://localhost:8000/docs`
- **OpenAPI schema:** `http://localhost:8000/openapi.json`

### Common startup errors

| Error | Fix |
|---|---|
| `could not connect to server` | PostgreSQL is not running. Start it with `pg_ctl start` or `brew services start postgresql`. |
| `password authentication failed` | Wrong credentials in `DATABASE_URL` in `.env`. |
| `ModuleNotFoundError: No module named 'api'` | Run from the project root, or check that `__init__.py` files exist in all `api/` subdirectories. |
| `httpx.ConnectError` during a run | Ollama is not running. Start it with `ollama serve`. |

---

## Decision Pipeline

### Step 1 — Create a Problem

```http
POST /problems
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "title": "Should we expand to Europe in Q3?",
  "visibility": "private"
}
```

Returns a `problem_id`. A Problem is a logical container — the question itself.

### Step 2 — Trigger a Run

```http
POST /problems/{problem_id}/runs
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "problem_text": "We have 12 months of runway, 3 enterprise clients in Germany, and need to decide whether to open a Berlin office in Q3..."
}
```

Returns **HTTP 202** immediately with a `run_id` and `status: "pending"`. The pipeline executes in the background.

### Step 3 — Poll for Completion

```http
GET /runs/{run_id}
Authorization: Bearer <access_token>
```

Repeat until `status` is `"done"` or `"failed"`. The `final_decision` field is populated on `"done"`.

### Step 4 — Retrieve Agent Outputs

```http
GET /runs/{run_id}/agents
Authorization: Bearer <access_token>
```

Returns the Sales, Operations, Finance, and Hades outputs — each with the exact prompt used, latency in milliseconds, and token count.

### Background Pipeline Sequence

```
_execute_run  (background task via asyncio.create_task)
  1. Load active AgentConfig rows for this org (or fall back to defaults)
  2. sales agent    → call Ollama → persist AgentOutput row
  3. ops agent      → call Ollama → persist AgentOutput row
  4. finance agent  → call Ollama → persist AgentOutput row
  5. hades agent    → call Ollama with all three outputs → persist AgentOutput row
  6. Set run.final_decision, run.status = "done", run.completed_at
```

If any Ollama call fails, the entire run is marked `"failed"`.

---

## API Reference

All endpoints require `Authorization: Bearer <access_token>` unless stated otherwise.

### Authentication

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | None | Create user + org atomically. Returns token pair. |
| `POST` | `/auth/login` | None | Verify password. Returns token pair. |
| `POST` | `/auth/refresh` | None | Rotate refresh token. Send `{ "refresh_token": "..." }`. |
| `POST` | `/auth/logout` | Bearer | Revoke refresh token. |
| `GET` | `/auth/me` | Bearer | Returns current user profile. |

### Problems

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/problems` | Bearer | List your problems. Supports `?visibility=private\|org\|public&page=1&page_size=20`. |
| `POST` | `/problems` | Bearer | Create a problem. |
| `GET` | `/problems/{id}` | Bearer | Get one problem. |
| `PATCH` | `/problems/{id}` | Bearer | Update title or visibility (owner only). |
| `DELETE` | `/problems/{id}` | Bearer | Soft-delete (owner only). Sets `is_deleted = true`. |

### Runs

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/problems/{id}/runs` | Bearer | Trigger a run. Returns 202. |
| `GET` | `/problems/{id}/runs` | Bearer | List all run versions for a problem. |
| `GET` | `/runs/{run_id}` | Bearer | Get run status and final decision. |
| `GET` | `/runs/{run_id}/agents` | Bearer | Get all four agent outputs for a run. |

### Feedback & Comments

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/runs/{run_id}/feedback` | Bearer | Submit per-agent rating. One per user per run. |
| `GET` | `/runs/{run_id}/feedback` | Bearer | Get your feedback for a run. |
| `GET` | `/runs/{run_id}/comments` | Bearer | List all comments. |
| `POST` | `/runs/{run_id}/comments` | Bearer | Post a comment. Use `parent_id` for threaded replies. |
| `PATCH` | `/comments/{comment_id}` | Bearer | Edit your comment. |
| `DELETE` | `/comments/{comment_id}` | Bearer | Delete a comment. |

### Sharing

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/runs/{run_id}/share` | Bearer | Create a share link (public or org-scoped, optional expiry). |
| `GET` | `/runs/{run_id}/share` | Bearer | Get the active share for a run. |
| `DELETE` | `/runs/{run_id}/share` | Bearer | Revoke the share link. |
| `GET` | `/shared/{share_token}` | **None** | Public view of a shared run. Returns `410` if expired. |

### Analytics

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/analytics/me` | Bearer | Personal stats: runs, avg rating, most trusted agent, runs this month. |
| `GET` | `/analytics/org` | Bearer | Org-wide stats: total runs, active users, most trusted agent. |
| `GET` | `/analytics/org/agents` | Bearer | Agent trust trend from the pre-aggregated trust score table. |

### Admin

All admin routes require `role = admin`. Agent config routes require `admin` or `is_power_user = true`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/admin/users` | Admin | List all users (paginated). |
| `PATCH` | `/admin/users/{id}` | Admin | Change user role, power_user flag, or org. |
| `GET` | `/admin/orgs` | Admin | List all organisations. |
| `PATCH` | `/admin/orgs/{id}` | Admin | Update org name or plan. |
| `GET` | `/admin/agent-configs` | Power/Admin | List agent configs for your org. |
| `POST` | `/admin/agent-configs` | Power/Admin | Create a new config version. |
| `PATCH` | `/admin/agent-configs/{id}/activate` | Power/Admin | Activate a config version (deactivates previous). |
| `GET` | `/admin/agent-configs/{id}/runs` | Power/Admin | List runs that used a specific config version. |
| `GET` | `/admin/analytics/platform` | Admin | Platform-wide counts: total runs, users, orgs. |
| `GET` | `/admin/analytics/usage-events` | Admin | Raw usage event log, filterable by event type and org. |

---

## Agent Configuration

By default the system runs with hardcoded prompts in `run_service.py`. To customise an agent for your org:

```http
# 1. Create a new config version
POST /admin/agent-configs
Authorization: Bearer <admin_token>
Content-Type: application/json

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

Config is loaded fresh for every run — activating a new version takes effect immediately on the next triggered run with no server restart required.

---

## Authentication

### Registration

The first user to register an org is automatically granted `role = admin` and `is_power_user = true`.

```json
POST /auth/register
{
  "email": "you@example.com",
  "password": "secure-password",
  "display_name": "Your Name",
  "org_name": "Acme Corp",
  "org_slug": "acme"
}
```

### Token Lifecycle

| Token | Validity | Usage |
|---|---|---|
| Access token | 30 minutes | `Authorization: Bearer <token>` on every request |
| Refresh token | 30 days | Send to `POST /auth/refresh` to get a new pair |

- **Rotation:** Every refresh call revokes the old token and issues a new one (one-time use)
- **Logout:** Sends refresh token to `POST /auth/logout`, revokes it server-side
- **Storage:** Refresh tokens are stored as SHA-256 hashes — the raw token is never persisted

---

## Rate Limits

| Scope | Endpoint Group | Limit |
|---|---|---|
| Per user | General | 60 requests / 60s |
| Per user | Run trigger | 10 requests / 1h |
| Per org | General | 300 requests / 60s |
| Per org | Run trigger | 50 requests / 1h |
| Per IP | Public endpoints | 20 requests / 60s |

---

## Roadmap

### In Progress
- [ ] Nightly `agent_trust_scores` aggregation job (APScheduler job defined, SQL documented in README)
- [ ] Frontend rebuild — full React SPA using the versioned API

### Planned
- [ ] Alembic migrations for schema evolution
- [ ] Webhook notifications on run completion
- [ ] OpenAI / Anthropic model support alongside Ollama
- [ ] Custom agent types (Legal, HR, Technical)
- [ ] SSO / SAML enterprise authentication
- [ ] Slack and Microsoft Teams integration

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m 'feat: add your feature'`
4. Push to the branch: `git push origin feature/your-feature`
5. Open a Pull Request

Please follow [Conventional Commits](https://www.conventionalcommits.org/) for commit messages.

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**Built with** [FastAPI](https://fastapi.tiangolo.com/) · [PostgreSQL](https://www.postgresql.org/) · [SQLAlchemy 2.0](https://www.sqlalchemy.org/) · [Ollama](https://ollama.ai/)

</div>