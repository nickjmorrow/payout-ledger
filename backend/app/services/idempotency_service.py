"""Making a repeated request one request.

The key is the primary key, so the uniqueness constraint is the lock. Four
answers: Proceed, Replay, InFlightError and KeyConflictError. Nothing here
commits: the key and the work it guards are one transaction. See AGENTS.md >
Idempotency.
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
    """The first request with this key has not finished; ask again shortly."""


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
    """A hash of a request body that ignores key order and whitespace."""
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

    `ON CONFLICT DO NOTHING`, so there is no window between checking and inserting.
    A concurrent request waits on the first one's uncommitted row, then replays it
    or takes the key.
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

    # Scoped per endpoint, so a key cannot replay an unrelated operation's response.
    if existing.endpoint != endpoint:
        raise KeyConflictError(
            f"This idempotency key was used for {existing.endpoint}, not {endpoint}."
        )

    if existing.request_fingerprint != digest:
        raise KeyConflictError(
            "This idempotency key was already used for a different request body. "
            "Reusing a key with new content would silently discard this request."
        )

    if existing.response_status is None:
        raise InFlightError("This request is still being processed. Retry shortly.")

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
    """Store what this request returned, in the same transaction as the work."""
    row = await session.get(IdempotencyKey, key)
    if row is None:
        raise IdempotencyError(f"no idempotency key {key!r} to record a response against")

    row.response_status = status
    row.response_body = body
    row.transfer_id = transfer_id
    await session.flush()
