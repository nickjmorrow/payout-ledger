"""Making a repeated request one request.

Retries are not optional. Networks drop responses, people double-click, and a
client that gave up waiting has no way to know whether its disbursement
happened. Without this, every one of those is a second payment.

The mechanism is the primary key. A client supplies a key; the first request to
arrive with it inserts the row and does the work; every later request with the
same key finds the row and is served the stored response instead of doing the
work again. There is no lock, no extra round trip and no window — **the
uniqueness constraint is the lock**, and exactly one INSERT can win.

Three things a caller can be told, and they are genuinely different:

  - `Proceed` — this key is new, do the work.
  - `Replay` — this key already completed, here is what it returned.
  - `InFlightError` — this key is being worked on right now by somebody else.
  - `KeyConflictError` — this key was used for a *different* request.

The last one matters more than it looks. Returning the first response for a
different body would silently discard the second request, which is the exact
failure an idempotency key exists to prevent — the client would believe it had
sent something it had not. It is refused instead.

**Nothing here commits.** The key row and the work it guards are one
transaction: a key recorded without its transfer would permanently block the
retry that would have created it, and a transfer without its key would be
payable twice.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import IdempotencyKey

logger = get_logger(__name__)


class IdempotencyError(Exception):
    """Base for every refusal in this module."""


class InFlightError(IdempotencyError):
    """The first request with this key has not finished yet.

    The honest answer is "ask again shortly", not a guess at what the other
    request will return. A client that retries gets the stored response once
    the first one lands.
    """


class KeyConflictError(IdempotencyError):
    """This key was already used for a materially different request."""


@dataclass(frozen=True)
class Proceed:
    """The key is new. Do the work, then call `record_response`."""


@dataclass(frozen=True)
class Replay:
    """The key already completed. Return this instead of doing the work again."""

    status: int
    body: dict[str, Any]
    transfer_id: uuid.UUID | None


def fingerprint(body: dict[str, Any]) -> str:
    """A stable hash of a request body.

    `sort_keys` because JSON object order is not meaningful and two clients
    serialising the same request must agree. `separators` because whitespace is
    not meaningful either, and a pretty-printed body is the same request as a
    compact one.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def claim(
    session: AsyncSession,
    *,
    key: str,
    endpoint: str,
    body: dict[str, Any],
) -> Proceed | Replay:
    """Take this key, or report what the request that already has it did.

    `ON CONFLICT DO NOTHING` rather than a SELECT followed by an INSERT. The
    read-then-write version has a window between the two in which a second
    request sees nothing and also inserts, and the whole point of this module
    is that that window does not exist.

    **A concurrent request blocks here rather than racing**, and that is
    correct. Postgres makes the second INSERT wait on the first transaction's
    uncommitted row: if that transaction commits, the waiter then sees the
    completed key and replays it; if it rolls back, the waiter takes the key
    and does the work itself. Either way the outcome is one payment, and the
    waiting is bounded by the first request's transaction.
    """
    digest = fingerprint(body)

    result = await session.execute(
        pg_insert(IdempotencyKey)
        .values(key=key, endpoint=endpoint, request_fingerprint=digest)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(IdempotencyKey.key)
    )
    if result.scalar_one_or_none() is not None:
        logger.info("idempotency key claimed", key=key, endpoint=endpoint)
        return Proceed()

    existing = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
    ).scalar_one()

    # Scoped per endpoint so a key cannot replay the response of an unrelated
    # operation — a `POST /transfers` key reused against a refund must not come
    # back with the transfer's 201.
    if existing.endpoint != endpoint:
        raise KeyConflictError(
            f"idempotency key {key!r} was used for {existing.endpoint!r}, not {endpoint!r}"
        )

    if existing.request_fingerprint != digest:
        raise KeyConflictError(
            f"idempotency key {key!r} was already used for a different request body; "
            "reusing a key with new content would silently discard this request"
        )

    if existing.response_status is None:
        raise InFlightError(f"idempotency key {key!r} is still being processed; retry shortly")

    logger.info("idempotency key replayed", key=key, endpoint=endpoint)
    return Replay(
        status=existing.response_status,
        body=existing.response_body or {},
        transfer_id=existing.transfer_id,
    )


async def record_response(
    session: AsyncSession,
    *,
    key: str,
    status: int,
    body: dict[str, Any],
    transfer_id: uuid.UUID | None = None,
) -> None:
    """Store what this request returned, so a retry can be served it.

    Called inside the same transaction as the work, so the response and the
    thing it describes become visible together.
    """
    row = await session.get(IdempotencyKey, key)
    if row is None:
        raise IdempotencyError(f"no idempotency key {key!r} to record a response against")

    row.response_status = status
    row.response_body = body
    row.transfer_id = transfer_id
    await session.flush()
