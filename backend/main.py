"""FastAPI application factory and default application instance."""
from fastapi import FastAPI
from backend.api.routes import router
from backend.app_state import initialize_state


def create_app(*, state=None) -> FastAPI:
    app = FastAPI(title="Wilderness North Fulfillment API", version="0.1.0")
    app.state.planning = state if state is not None else initialize_state()
    app.include_router(router)
    return app


app = create_app()
