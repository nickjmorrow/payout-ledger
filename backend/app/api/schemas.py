"""The HTTP response envelope, the request bodies, and the HTTP-only resources.

**Every response is `{"data": ..., "meta": ...}`.** A bare array or scalar at
the top level leaves nowhere to add pagination, warnings, or a deprecation
notice without breaking clients.

What does *not* belong here is anything the worker also publishes: those shapes
live in `app/wire.py`, because a process that serves no requests should not
have to import the HTTP layer to say what a frame looks like. The shapes below
are the ones that genuinely only travel over HTTP, and they inherit camelCase
from `wire.ApiSchema` along with everything else on the wire.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.wire import ApiSchema


class Meta(ApiSchema):
    status: Literal["success"] = "success"


class ApiResponse[T](BaseModel):
    data: T
    meta: Meta = Meta()


# ---------------------------------------------------------------- resources


class TaskOut(ApiSchema):
    id: UUID
    kind: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    attempts: int
    created_at: datetime
