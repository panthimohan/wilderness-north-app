# Wilderness North App

A web-based planning tool for the **Wilderness North** challenge. Operators import orders and capacity data, track picking progress, and build tote, cart, and flight plans with flight cost tracking in a single planning session.

> **Deployment scope:** this release targets a restricted, **single-operator hackathon deployment**. It is not a multi-user service. See [Deployment](#deployment) and [DEPLOYMENT.md](DEPLOYMENT.md).

## Features

- Import orders and capacity data from spreadsheet files (up to 5 files, 5 MiB each)
- Track picking progress
- Plan totes, carts, and flights
- Track flight costs
- Challenge CSV data loaded from `data/Wilderness North/` at startup
- Single-operator lease so a second browser can't overwrite an active session
- Health endpoint (`GET /health`) and interactive API docs (`/docs`)

<!-- TODO: add screenshots or a short GIF of the UI here -->

## Tech stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.10+, [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [openpyxl](https://openpyxl.readthedocs.io/) |
| Frontend | TypeScript, [Vite](https://vite.dev/) (Node.js 22) |
| Hosting config | `render.yaml` (Render) |

## Project structure

````
wilderness-north-app/
├── backend/               # FastAPI application (backend.main:app)
├── frontend/              # Vite frontend (builds to frontend/dist/)
├── data/Wilderness North/ # Challenge CSV files read by the backend at startup
├── tests/                 # Test suite
├── DEPLOYMENT.md          # Production deployment guide
├── render.yaml            # Render deployment config
├── requirements.txt       # Runtime Python dependencies
└── requirements-dev.txt   # Development/test dependencies
````

## Getting started (local development)

### Prerequisites

- Python 3.10 or newer
- Node.js 22
- Linux or macOS (the production process lock uses `fcntl.flock`)

### 1. Clone the repository

````bash
git clone https://github.com/panthimohan/wilderness-north-app.git
cd wilderness-north-app
````

### 2. Backend

````bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt        # add -r requirements-dev.txt for tests

python -m uvicorn backend.main:app --reload --port 8000
````

The API is now at `http://localhost:8000` with docs at `http://localhost:8000/docs`.

### 3. Frontend

In a second terminal:

````bash
cd frontend
npm ci
npm run dev
````

The Vite dev server proxies API requests to the backend. <!-- TODO: confirm dev URL/port (Vite default is http://localhost:5173) -->

## Running tests

````bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
````

<!-- TODO: confirm the test runner and add the frontend test command if one exists -->

## Configuration

All settings are environment variables. No API keys or secrets are required.

| Variable | Default | Description |
| --- | --- | --- |
| `DEPLOYMENT_MODE` | `development` | Set to `single_operator` in production to enable the operator lease and process lock |
| `HOST` | `0.0.0.0` | Uvicorn bind address (via the start command) |
| `PORT` | `8000` | Uvicorn port (via the start command) |
| `CORS_ALLOWED_ORIGINS` | Vite dev origins | Comma-separated exact frontend origins. Wildcards are rejected. Empty for same-origin proxying |
| `OPERATOR_SESSION_TIMEOUT_MINUTES` | `240` | Idle lease expiry (minimum 5) |
| `OPERATOR_PROCESS_LOCK_PATH` | `/tmp/wilderness-north-operator.lock` | Host-local lock file |
| `OPERATOR_COOKIE_SECURE` | `true` in `single_operator` mode | Requires HTTPS in production |
| `OPERATOR_COOKIE_SAMESITE` | `lax` | Use `none` (with secure cookies) for unrelated frontend/backend sites |
| `VITE_API_BASE_URL` | unset | **Build-time** frontend setting. Leave unset for same-origin `/api` requests |

## Deployment

Production runs as exactly **one application replica** behind a trusted access boundary such as a private network or authenticated proxy.

````bash
# Build the frontend
cd frontend && npm ci && npm run build && cd ..

# Start the API (one worker only)
export DEPLOYMENT_MODE=single_operator
export CORS_ALLOWED_ORIGINS=https://your-frontend-origin
.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
````

Then serve `frontend/dist/` as static content, route `/api/` and `/health` to the backend, and serve `index.html` for all other paths.

Key points:

- Do not increase `--workers` or the replica count. A second worker on the same host fails at startup.
- Set the proxy body limit to at least **36 MiB** (for example, Nginx `client_max_body_size 36m`).
- Use HTTPS in production.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full guide.

## Limitations

- **Single operator only.** The operator lease prevents overwrites but is **not authentication**.
- **No persistence.** State and imported data live in memory and are lost on restart or redeploy. Export anything you need to keep before restarting.
- Challenge CSV files reload from `data/Wilderness North/` after every restart.

## Contributing

Issues and pull requests are welcome. Please run the test suite before submitting changes.
