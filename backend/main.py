"""FastAPI application factory and default application instance."""
from __future__ import annotations

import fcntl
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from typing import Sequence

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import config
from backend.api.routes import router
from backend.app_state import initialize_state

OPERATOR_SESSION_CONFLICT = "Another active operator session is using this planning workspace."


def _acquire_process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(
            "A Wilderness North single-operator backend is already running on this host. "
            "Run exactly one backend process/worker."
        ) from exc
    return handle


def create_app(
    *,
    state=None,
    deployment_mode: str | None = None,
    cors_allowed_origins: Sequence[str] | None = None,
    operator_process_lock_path: str | Path | None = None,
    operator_session_timeout_minutes: int | None = None,
    operator_cookie_secure: bool | None = None,
    operator_cookie_samesite: str | None = None,
) -> FastAPI:
    mode = deployment_mode or config.DEPLOYMENT_MODE
    if mode not in {"development", "single_operator"}:
        raise ValueError("deployment_mode must be 'development' or 'single_operator'")
    origins = tuple(config.CORS_ALLOWED_ORIGINS if cors_allowed_origins is None else cors_allowed_origins)
    if "*" in origins:
        raise ValueError("CORS origins must be explicit; wildcard origins are not allowed")
    session_timeout = (
        config.OPERATOR_SESSION_TIMEOUT_MINUTES
        if operator_session_timeout_minutes is None
        else operator_session_timeout_minutes
    )
    if session_timeout < 5:
        raise ValueError("operator session timeout must be at least 5 minutes")
    cookie_secure = config.OPERATOR_COOKIE_SECURE if operator_cookie_secure is None else operator_cookie_secure
    cookie_samesite = (operator_cookie_samesite or config.OPERATOR_COOKIE_SAMESITE).lower()
    if cookie_samesite not in {"lax", "strict", "none"}:
        raise ValueError("operator cookie SameSite must be lax, strict, or none")
    if cookie_samesite == "none" and not cookie_secure:
        raise ValueError("SameSite=None operator cookies require Secure=true")
    process_lock_path = Path(operator_process_lock_path or config.OPERATOR_PROCESS_LOCK_PATH)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lock_handle = _acquire_process_lock(process_lock_path) if mode == "single_operator" else None
        app.state.process_lock_handle = lock_handle
        try:
            yield
        finally:
            if lock_handle is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
                app.state.process_lock_handle = None

    app = FastAPI(title="Wilderness North Fulfillment API", version="0.1.0", lifespan=lifespan)
    app.state.planning = state if state is not None else initialize_state()
    app.state.deployment_mode = mode
    app.state.operator_session_id = None
    app.state.operator_session_last_seen = 0.0
    app.state.operator_session_lock = RLock()
    app.state.operator_session_timeout_seconds = session_timeout * 60
    app.state.operator_cookie_secure = cookie_secure
    app.state.operator_cookie_samesite = cookie_samesite
    app.state.operator_cookie_name = config.OPERATOR_COOKIE_NAME

    @app.middleware("http")
    async def enforce_single_operator(request: Request, call_next):
        if mode != "single_operator" or not request.url.path.startswith("/api/") or request.method == "OPTIONS":
            return await call_next(request)

        now = time.monotonic()
        cookie_token = request.cookies.get(config.OPERATOR_COOKIE_NAME)
        with app.state.operator_session_lock:
            active_token = app.state.operator_session_id
            expired = (
                active_token is not None
                and now - app.state.operator_session_last_seen > app.state.operator_session_timeout_seconds
            )
            if expired:
                active_token = None
                app.state.operator_session_id = None
            if active_token is not None and not secrets.compare_digest(active_token, cookie_token or ""):
                return JSONResponse(status_code=423, content={"detail": OPERATOR_SESSION_CONFLICT})
            if active_token is None:
                active_token = secrets.token_urlsafe(32)
                app.state.operator_session_id = active_token
            app.state.operator_session_last_seen = now

        response = await call_next(request)
        # Refresh the browser cookie on each authorized API response so active
        # use can continue past the lease duration without losing its token.
        response.set_cookie(
            key=config.OPERATOR_COOKIE_NAME,
            value=active_token,
            max_age=app.state.operator_session_timeout_seconds,
            httponly=True,
            secure=app.state.operator_cookie_secure,
            samesite=app.state.operator_cookie_samesite,
            path="/",
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.include_router(router)
    return app


app = create_app()
