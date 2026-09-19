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

from pydantic import BaseModel, Field

from app.config import settings
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


class TransferOut(ApiSchema):
    id: UUID
    recipient_id: UUID
    recipient_name: str
    amount_minor: int
    currency: str
    status: Literal["pending", "processing", "succeeded", "failed"]
    provider_reference: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class AccountOut(ApiSchema):
    id: UUID
    name: str
    kind: str
    currency: str
    # Derived from the entries on every read, never stored. See models.Account.
    balance_minor: int


class RecipientOut(ApiSchema):
    id: UUID
    full_name: str
    msisdn: str
    country: str
    enrolled_at: datetime


# ---------------------------------------------------------------- requests


class TransferIn(ApiSchema):
    recipient_id: UUID
    # `gt=0` here as well as a CHECK in the database. This one produces a 422
    # the client can show; the CHECK is what makes it true regardless of who is
    # writing. Neither makes the other redundant.
    amount_minor: int = Field(gt=0, le=settings.max_transfer_minor)
    currency: str = Field(min_length=3, max_length=3)
