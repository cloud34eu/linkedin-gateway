# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LinkedIn Gateway is an unofficial LinkedIn API gateway. A Chrome extension captures the user's LinkedIn session, relays it to a self-hosted FastAPI backend via WebSocket, and exposes a clean REST API that applications can consume.

## Commands

### Backend

```bash
cd backend

# Install dependencies
uv sync

# Run database migrations
uv run alembic upgrade head

# Start dev server (from backend/)
uv run uvicorn main:app --host 0.0.0.0 --port 7778 --reload

# Run tests
uv run pytest
uv run pytest tests/path/to/test_file.py::test_function_name  # single test

# Debug raw LinkedIn responses (writes to backend/debug_responses/)
DEBUG_LINKEDIN_RESPONSES=true uv run uvicorn main:app --reload
```

### Chrome Extension

```bash
cd chrome-extension
npm install

npm run build         # production build (webpack.config.js)
npm run dev           # development watch mode
npm run build:dev     # build with webpack.config.v2.js → dist-dev/
npm run build:prod    # build with webpack.config.prod.v2.js
```

Load in Chrome from `chrome-extension/dist-dev/` (developer mode, "Load unpacked").

### Docker

```bash
# Standard deployment
cd deployment
docker compose up -d

# Dev with hot reload
docker compose -f docker-compose.dev.yml up

# Enterprise edition
docker compose -f docker-compose.enterprise.yml up
```

### Database Migrations

```bash
cd backend
uv run alembic revision --autogenerate -m "description"  # generate migration
uv run alembic upgrade head                               # apply
uv run alembic downgrade -1                              # rollback one
```

## Environment Configuration

Copy `deployment/.env.example` to `deployment/.env`. Required vars:

| Variable | Description |
|---|---|
| `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_HOST`, `DB_PORT` | PostgreSQL credentials |
| `JWT_SECRET_KEY` | Generate with `openssl rand -hex 32` |
| `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | From LinkedIn Developer App |
| `LG_BACKEND_EDITION` | `core` (default), `saas`, or `enterprise` |
| `LG_CHANNEL` | `default` or `railway_private` |
| `PUBLIC_URL` | Public HTTPS URL needed for OAuth callback |

The backend also accepts `DATABASE_URL` as a full connection string instead of individual `DB_*` vars.

## Architecture

### Request Flow

Two execution modes exist for every LinkedIn API endpoint, controlled by a `server_call` boolean in the request body:

- **WebSocket mode** (`server_call=false`, default): Backend sends a `REQUEST_*` message over the persistent WebSocket to the Chrome extension. The extension executes the LinkedIn API call in-browser (with full cookie context), returns a `RESPONSE_*` message, and the backend relays the result to the HTTP caller. Uses `pending_ws_requests` dict with asyncio Events for synchronization (`backend/app/ws/state.py`).

- **Server-side mode** (`server_call=true`): Backend calls LinkedIn's Voyager API directly using stored CSRF token and cookies from the database. Implemented in `backend/app/linkedin/services/base.py` via `httpx`. Only the essential cookies (`li_at`, `JSESSIONID`) are sent to avoid volatile tracking cookies.

### WebSocket Connection Model

WebSocket connections (`/ws`) are keyed by `instance_id` (browser extension installation), **not** by user ID. Users can log in/out without affecting the WebSocket connection. Authentication over WebSocket is optional and only used for tracking which user owns an instance. The connection manager lives in `backend/app/ws/connection_manager.py`.

### Multi-Key Support (v1.1.0)

Each user can have multiple API keys, each associated with a specific browser instance (`instance_id`, `instance_name`). Each key stores its own `csrf_token` and `linkedin_cookies`. The helper `get_linkedin_service()` in `backend/app/linkedin/helpers/server_call.py` handles both multi-key (passing `APIKey` object) and legacy single-key (passing `UUID`) modes.

### Edition System

`backend/app/core/edition.py` defines three editions via `LG_BACKEND_EDITION`:
- **core**: Self-hosted, no restrictions, local accounts enabled.
- **saas**: Cloud-hosted; disables server-side execution and local accounts; acts as licensing server for enterprise.
- **enterprise**: Self-hosted premium; requires license validation against the SaaS server.

Edition-specific plugins are loaded at startup from `app/saas_plugins/` or `app/enterprise_plugins/` (the enterprise stub is included; saas plugins are closed-source).

### Backend Module Layout

```
backend/
├── main.py                    # App entrypoint, WebSocket handler, router registration
├── app/
│   ├── core/
│   │   ├── config.py          # Pydantic Settings (env vars)
│   │   ├── edition.py         # Edition/feature matrix detection
│   │   └── security.py        # Password hashing, JWT, session creation
│   ├── auth/
│   │   ├── oauth.py           # LinkedIn OAuth flow (Authlib)
│   │   ├── local_auth.py      # Email/password auth (core/enterprise only)
│   │   ├── api_key.py         # API key validation
│   │   └── dependencies.py    # FastAPI auth dependencies
│   ├── db/
│   │   ├── models/            # SQLAlchemy ORM models
│   │   └── session.py         # Engine + get_db() dependency
│   ├── api/v1/                # HTTP endpoint routers (one file per resource)
│   ├── linkedin/
│   │   ├── services/base.py   # LinkedInServiceBase: httpx client, headers, cookie filtering
│   │   ├── services/          # Per-resource services (profile, feed, messages, etc.)
│   │   ├── helpers/
│   │   │   ├── server_call.py # get_linkedin_service() factory
│   │   │   └── proxy_http.py  # Generic HTTP proxy via WebSocket
│   │   └── utils/             # Profile ID extraction, parsers
│   ├── gemini/                # Gemini AI proxy (v1.2.0) – OAuth + chat completions
│   ├── ws/
│   │   ├── message_types.py   # MessageType enum + MessageSchema factories
│   │   ├── connection_manager.py
│   │   ├── state.py           # pending_ws_requests shared dict
│   │   └── events.py          # WebSocketEventHandler
│   └── schemas/               # Pydantic request/response schemas
└── alembic/                   # Migration environment + versions
```

### LinkedIn API Details

All server-side LinkedIn calls target `https://www.linkedin.com/voyager/api` (REST) or `/voyager/api/graphql` (GraphQL/Voyager). The base service class sets LinkedIn-specific headers (`csrf-token`, `x-restli-protocol-version`, `accept: application/vnd.linkedin.normalized+json+2.1`) and only sends `li_at` + `JSESSIONID` (+ optionally `liap`) cookies to avoid frequent session invalidation from volatile analytics cookies.

### Chrome Extension

Webpack-based MV3 extension. Source is in `chrome-extension/src-v2/` (v2 configs) and legacy `src/`. The extension:
1. Captures LinkedIn session cookies and CSRF token on login.
2. Maintains a persistent WebSocket connection to the backend, identified by a stable `instance_id`.
3. Executes backend-requested LinkedIn API calls in-browser via `fetch` with `credentials: 'include'`.
4. Reports the captured credentials back to the backend for storage per API key.
