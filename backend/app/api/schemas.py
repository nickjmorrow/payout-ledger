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

from pydantic import BaseModel, Field, model_validator

from app.config import settings
from app.models import JournalEntry, LedgerEntry, Task, Transfer
from app.money import format_money
from app.services.run_service import RunSummary
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
    status: Literal["pending", "running", "succeeded", "failed"]
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


class QueueOut(ApiSchema):
    """The queue right now: how much of each kind of work, and what it is."""

    due: int
    scheduled: int
    running: int
    dead: int
    active: list[TaskOut]


class TransferOut(ApiSchema):
    id: UUID
    recipient_id: UUID
    recipient_name: str
    amount_minor: int
    currency: str
    status: Literal["pending", "processing", "succeeded", "failed"]
    provider_reference: str | None
    failure_reason: str | None
    run_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_transfer(cls, transfer: Transfer) -> "TransferOut":
        return cls(
            id=transfer.id,
            recipient_id=transfer.recipient_id,
            recipient_name=transfer.recipient.full_name,
            amount_minor=transfer.amount_minor,
            currency=transfer.currency,
            status=transfer.status,  # pyright: ignore[reportArgumentType]
            provider_reference=transfer.provider_reference,
            failure_reason=transfer.failure_reason,
            run_id=transfer.run_id,
            created_at=transfer.created_at,
            updated_at=transfer.updated_at,
        )


class RunOut(ApiSchema):
    """A payment run and its progress, counted from its transfers when asked."""

    id: UUID
    memo: str | None
    currency: str
    created_at: datetime
    count: int
    total_minor: int
    # Transfer status -> how many of this run's transfers are in it.
    by_status: dict[str, int]

    @classmethod
    def from_summary(cls, summary: RunSummary) -> "RunOut":
        return cls(
            id=summary.run.id,
            memo=summary.run.memo,
            currency=summary.run.currency,
            created_at=summary.run.created_at,
            count=summary.count,
            total_minor=summary.total_minor,
            by_status=summary.by_status,
        )


class LineOut(ApiSchema):
    """One side of a posting. `amount_minor` is positive; `direction` is the sign."""

    account_id: UUID
    account_kind: str
    account_name: str
    direction: Literal["debit", "credit"]
    amount_minor: int
    currency: str

    @classmethod
    def from_line(cls, line: LedgerEntry) -> "LineOut":
        return cls(
            account_id=line.account_id,
            account_kind=line.account.kind,
            account_name=line.account.name,
            direction=line.direction,  # pyright: ignore[reportArgumentType]
            amount_minor=line.amount_minor,
            currency=line.currency,
        )


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

    @classmethod
    def from_journal(cls, journal: JournalEntry) -> "JournalOut":
        return cls(
            id=journal.id,
            kind=journal.kind,
            memo=journal.memo,
            created_at=journal.created_at,
            lines=[LineOut.from_line(line) for line in journal.lines],
        )


class TransferDetailOut(TransferOut):
    """A transfer with its two histories: what the books say, and what the worker did.

    Both in one response rather than two endpoints, for the same reason the
    overview is one request: they are read side by side, and a journal from one
    moment beside a task list from another would show a settlement whose task
    had apparently not run yet.
    """

    journals: list[JournalOut]
    tasks: list[TaskOut]

    @classmethod
    def from_parts(
        cls, transfer: Transfer, journals: list[JournalEntry], tasks: list[Task]
    ) -> "TransferDetailOut":
        return cls(
            **TransferOut.from_transfer(transfer).model_dump(),
            journals=[JournalOut.from_journal(journal) for journal in journals],
            tasks=[TaskOut.from_task(task) for task in tasks],
        )


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


# ---------------------------------------------------------------- requests


def _within_cap(amount_minor: int, currency: str) -> None:
    """The per-payment ceiling, refused in words the console can show as they are."""
    if amount_minor > settings.max_transfer_minor:
        cap = format_money(settings.max_transfer_minor, currency)
        raise ValueError(f"A single payment is capped at {cap}.")


class RunItemIn(ApiSchema):
    recipient_id: UUID
    amount_minor: int = Field(gt=0)


class RunIn(ApiSchema):
    # Bounded because a run is one transaction: its size is how long the
    # funding lock is held. See `max_run_size` in config.py.
    items: list[RunItemIn] = Field(min_length=1, max_length=settings.max_run_size)
    currency: str = Field(min_length=3, max_length=3)
    memo: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _items_within_cap(self) -> "RunIn":
        for item in self.items:
            _within_cap(item.amount_minor, self.currency)
        return self


class TransferIn(ApiSchema):
    recipient_id: UUID
    # `gt=0` here as well as a CHECK in the database. This one produces a 422
    # the client can show; the CHECK is what makes it true regardless of who is
    # writing. Neither makes the other redundant.
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _amount_within_cap(self) -> "TransferIn":
        _within_cap(self.amount_minor, self.currency)
        return self
