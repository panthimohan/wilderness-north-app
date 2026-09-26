# Wilderness North deployment guide

## Supported deployment target

This release supports a **restricted, single-operator hackathon deployment**. It is not multi-user: orders, imports, picking progress, tote/cart/flight plans, and flight costs share one in-memory planning session. The application enforces one active browser operator per backend process in `single_operator` mode and takes a host-local process lock so a second worker on that host fails startup. Deploy exactly one application replica. The active-browser lease expires after inactivity (four hours by default).

The lease is an overwrite-prevention mechanism, not authentication. Put the app behind a trusted access boundary (for example, the hackathon's private network or an authenticated access proxy). Do not expose it as a public multi-user service. State and uploaded datasets are lost on process restart/redeployment; there is no database or persistent session store.

## Prerequisites

- Linux deployment host (the single-process guard uses `fcntl.flock`).
- Python 3.10 or newer.
- Node.js 22 for the Vite 8 build.
- Include the repository's `data/Wilderness North/` directory in the backend image/package. Backend initialization reads the challenge CSV files from these repository-relative paths.
- Configure any reverse proxy/load balancer to send all requests from one operator browser to the same sole application instance.

## Environment

- `DEPLOYMENT_MODE=single_operator` enables the operator lease and process lock. Local development defaults to `development` and retains the Vite proxy workflow.
- `HOST` and `PORT` configure the Uvicorn bind address/port through the startup command (defaults `0.0.0.0` and `8000`).
- `CORS_ALLOWED_ORIGINS` is a comma-separated list of exact frontend origins, such as `https://example.org`. It defaults to the two Vite development origins. Wildcard origins are rejected. Same-origin reverse-proxy deployments can set it to an empty value; cross-origin deployments must list their exact frontend origin(s).
- `OPERATOR_SESSION_TIMEOUT_MINUTES` controls idle lease expiry (minimum 5; default 240).
- `OPERATOR_PROCESS_LOCK_PATH` sets the host-local lock file (default `/tmp/wilderness-north-operator.lock`). Use a local path shared by the processes on that host. This is not a distributed lock; deploy one replica.
- `OPERATOR_COOKIE_SECURE` defaults to true in `single_operator` mode. Production must use HTTPS. For unrelated frontend/backend sites, set `OPERATOR_COOKIE_SAMESITE=none` and keep `OPERATOR_COOKIE_SECURE=true`; otherwise leave the default `lax`.
- `VITE_API_BASE_URL` is a frontend **build-time** setting. Leave unset for same-origin relative `/api` requests. For a separate API origin, set it to that origin and configure matching CORS above. The browser client sends credentials for the operator-session cookie.

No API keys or application secrets are required by the current code.

## Install and build

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cd frontend
npm ci
npm run build
```

Serve `frontend/dist/` as static content. Configure the reverse proxy so `/api/` and `/health` reach the backend and other paths serve the frontend's `index.html`. The frontend development proxy in `vite.config.ts` is for local Vite development only.

## Start the production API

From the repository root, with the environment variables configured:

```bash
export DEPLOYMENT_MODE=single_operator
export HOST=0.0.0.0
export PORT=8000
export CORS_ALLOWED_ORIGINS=https://example.org

.venv/bin/python -m uvicorn backend.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --workers 1
```

Replace the example origin with the real frontend origin; do not include a path or trailing slash. For same-origin proxying, configure `CORS_ALLOWED_ORIGINS=` instead. Do not increase `--workers` or replica count. The process lock rejects a second worker on the same host; replicas on separate hosts are not coordinated.

## Reverse-proxy body size

Order/capacity import allows up to five files of 5 MiB each. Base64 JSON encoding makes the maximum request approximately 34 MiB. Set the proxy/request-body limit to at least 36 MiB (for example, Nginx `client_max_body_size 36m`). The application also enforces file, expanded workbook, row, and column limits.

## Health checks and restart behavior

Use `GET /health` for a non-sensitive liveness check. Use `GET /api/session` only from the active operator's browser; in single-operator mode it claims or renews the operator lease. The API exposes `/docs`; restrict it at the proxy if it should not be publicly browsable.

A restart releases the process lock but resets the in-memory planning session and removes imported data/progress/plans/costs. Challenge CSV files reload from the packaged repository data directory. Back up/export operational data outside this app before restarting if it must be retained.
