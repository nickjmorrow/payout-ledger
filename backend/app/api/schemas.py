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
from app.models import Task
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
    max_attempts: int
    run_at: datetime
    claimed_by: str | None
    error: str | None
    # The transfer this task is about, when it is about one. Read off the
    # payload so an operator can get from a stuck task to its payment.
    transfer_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_task(cls, task: Task) -> "TaskOut":
        raw = task.payload.get("transfer_id")
        return cls(
            id=task.id,
            kind=task.kind,
            status=task.status,  # pyright: ignore[reportArgumentType]
            attempts=task.attempts,
            max_attempts=task.max_attempts,
            run_at=task.run_at,
            claimed_by=task.claimed_by,
            error=task.error,
            transfer_id=UUID(raw) if isinstance(raw, str) else None,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


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


class LineOut(ApiSchema):
    """One side of a posting. `amount_minor` is positive; `direction` is the sign."""

    account_id: UUID
    account_kind: str
    account_name: str
    direction: Literal["debit", "credit"]
    amount_minor: int
    currency: str


class JournalOut(ApiSchema):
    """One balanced posting, lines and all, for a person to read.

    The lines are sent as they were written rather than summarised into a
    single signed amount, because the whole point of showing a journal is that
    the reader can see it balance: a debit here, a credit there, the same
    number on both.
    """

    id: UUID
    kind: str
    memo: str | None
    created_at: datetime
    lines: list[LineOut]


class TransferDetailOut(TransferOut):
    """A transfer with its two histories: what the books say, and what the worker did.

    Both in one response rather than two endpoints, for the same reason the
    overview is one request: they are read side by side, and a journal from one
    moment beside a task list from another would show a settlement whose task
    had apparently not run yet.
    """

    journals: list[JournalOut]
    tasks: list[TaskOut]


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


class FindingOut(ApiSchema):
    id: UUID
    kind: str
    transfer_id: UUID | None
    provider_reference: str | None
    detail: str
    healed: bool
    created_at: datetime


class OverviewOut(ApiSchema):
    accounts: list["AccountOut"]
    # Always zero. Surfaced rather than asserted only in tests, because a
    # non-zero value means a database trigger has gone missing and nothing else
    # would say so.
    trial_balance_minor: int
    unresolved_findings: int
    dead_lettered: int


class TransferIn(ApiSchema):
    recipient_id: UUID
    # `gt=0` here as well as a CHECK in the database. This one produces a 422
    # the client can show; the CHECK is what makes it true regardless of who is
    # writing. Neither makes the other redundant.
    amount_minor: int = Field(gt=0, le=settings.max_transfer_minor)
    currency: str = Field(min_length=3, max_length=3)
