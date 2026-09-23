"""Request ids and access logging.

Pure ASGI rather than `BaseHTTPMiddleware`, which buffers the response body and
would deliver the event stream in one lump. The request id is bound into
structlog's context, so every log line carries it.
"""

import time
import uuid
from typing import Any

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestContextMiddleware:
    """Tag every request, log both ends of it, and hand the id back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        # Keep an id from upstream, so one trace survives more than one hop.
        request_id = headers.get(REQUEST_ID_HEADER) or new_request_id()

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        method: str = scope.get("method", "")
        path: str = scope.get("path", "")
        started = time.perf_counter()
        logger.info("request started", method=method, path=path)

        async def send_with_context(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)

                # Time to first byte: for a stream, time to last byte is how long someone watched.
                logger.info(
                    "request finished",
                    method=method,
                    path=path,
                    status_code=message["status"],
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except Exception:
            logger.exception("request failed", method=method, path=path)
            raise
        finally:
            structlog.contextvars.clear_contextvars()


def current_request_id() -> str | None:
    """The id bound to this request, stored on tasks it enqueues so the worker can re-bind it."""
    bound: dict[str, Any] = structlog.contextvars.get_contextvars()
    value = bound.get("request_id")
    return str(value) if value else None
