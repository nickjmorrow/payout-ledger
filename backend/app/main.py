"""Application wiring: logging, middleware, routers. No logic."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middleware import RequestContextMiddleware
from app.api.routes import events, health, operations, runs, transfers
from app.bus import bus
from app.config import settings
from app.db import engine
from app.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
# `_app` is handed to us by FastAPI and not needed; the underscore says so.
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    logger.info("app starting")
    # One LISTEN connection for the process, shared by every open stream.
    await bus.start()
    yield
    await bus.stop()
    # Close pooled connections, or the container waits out its grace period.
    await engine.dispose()
    logger.info("app stopped")


app = FastAPI(title="Payout Ledger", version="0.1.0", lifespan=lifespan)

# Outermost, so the request id is bound before anything else runs. CORS
# matters only when the frontend runs outside Docker; in compose, /api is
# same-origin.
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(transfers.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(operations.router, prefix="/api")
app.include_router(events.router, prefix="/api")


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Turn an unhandled error into one logged traceback and one shaped 500."""
    logger.error("unhandled error", path=request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
