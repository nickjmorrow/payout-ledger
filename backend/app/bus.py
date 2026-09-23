"""Live fan-out from the worker to whoever is watching.

The worker and the API are separate processes, so the worker cannot hand a
token to an open HTTP response — there is no shared memory to hand it through.
Postgres already sits between them, and it has `LISTEN`/`NOTIFY`, so that is the
channel. No Redis, no message broker.

**Two channels, on purpose.** Durable state goes in a table and can be
re-read at any time. Progress notifications go through here, and are gone if
nobody was listening. That split is the whole design: a progress ping is not
worth a row and a row is not fast enough for a ping. Publish every durable
change on both paths, and a dropped frame costs a moment of staleness rather
than a corruption — the authoritative state is still one query away.

**Build the live path so that losing a frame is a flicker, never a
corruption.** Anything a viewer could not recover by re-reading the table does
not belong on this channel.

NOTIFY payloads are capped at 8000 bytes by Postgres. Status frames are far
below that; if you ever publish something large, publish a pointer to a row
instead.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging import get_logger
from app.wire import Event, Topic

logger = get_logger(__name__)

# Where every change notice goes, whichever process made the change. The
# console listens here. The worker's wake-up channel is a separate one in
# task_service, because "there is work" and "something changed" have different
# listeners with different reasons to wake.
EVENTS_CHANNEL = "ledger_events"

# Bounded so one stalled browser cannot grow a queue without limit. Overflow
# drops the oldest frames, which costs a flicker of live text and nothing more —
# the durable events behind it are still in the database.
SUBSCRIBER_QUEUE_SIZE = 512


def channel_for(prefix: str, subject_id: UUID) -> str:
    """A channel name for live updates about one row.

    Postgres channel names are identifiers, so no dashes and 63 characters max
    — which is why the uuid goes in as `.hex` rather than `str()`.
    """
    return f"{prefix}_{subject_id.hex}"


class Bus:
    """One LISTEN connection per process, fanned out in memory.

    The naive version opens a Postgres connection per open stream. That works
    until two people leave tabs open. One connection and a dict of subscribers
    costs the same code and does not fall over.
    """

    def __init__(self) -> None:
        self._connection: asyncpg.Connection | None = None
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    async def start(self) -> None:
        self._connection = await asyncpg.connect(settings.database_dsn)
        logger.info("bus connected")

    async def stop(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            logger.info("bus disconnected")

    def _on_notify(self, _conn: Any, _pid: int, channel: str, payload: str) -> None:
        # Called by asyncpg on the event loop. Must not block and must not
        # raise — an exception here is swallowed by the driver and the
        # subscriber simply never hears anything again.
        try:
            frame = json.loads(payload)
        except json.JSONDecodeError:
            logger.error("bus payload not json", channel=channel)
            return

        for queue in self._subscribers.get(channel, set()):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                logger.warning("bus subscriber lagging", channel=channel)

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncGenerator[asyncio.Queue[dict[str, Any]]]:
        """Receive frames published on `channel` for as long as the block runs."""
        if self._connection is None:
            raise RuntimeError("bus not started")

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        listeners = self._subscribers.setdefault(channel, set())

        # Only the first subscriber to a channel talks to Postgres; the rest
        # join the in-memory set.
        if not listeners:
            await self._connection.add_listener(channel, self._on_notify)
        listeners.add(queue)

        try:
            yield queue
        finally:
            listeners.discard(queue)
            if not listeners:
                self._subscribers.pop(channel, None)
                if self._connection is not None:  # pyright: ignore[reportUnnecessaryComparison]
                    await self._connection.remove_listener(channel, self._on_notify)


async def publish(session: AsyncSession, channel: str, frame: dict[str, Any]) -> None:
    """Send one frame to everyone listening on `channel`. **Does not commit.**

    Publishing goes through the ordinary session rather than the bus's own
    connection, and that is what makes it safe: **NOTIFY is transactional.**
    Postgres holds the notification until the surrounding transaction commits
    and discards it on rollback, so this can never announce something a reader
    cannot yet see — and cannot announce something that never happened.

    That property is the reason this does not commit, which it used to. The
    caller decides when the transaction ends, so a notification and the rows it
    refers to land together or not at all. Commit here and you get the opposite:
    a listener woken about a row that is still invisible, or worse, still
    hypothetical.
    """
    await session.execute(
        text("select pg_notify(:channel, :payload)"),
        {"channel": channel, "payload": json.dumps(frame)},
    )


# One per process. Started and stopped by the app lifespan in main.py, and by
# the worker in worker/loop.py.
bus = Bus()


async def announce(
    session: AsyncSession,
    *,
    topic: Topic,
    subject_id: UUID,
    transfer_id: UUID | str | None = None,
) -> None:
    """Tell every open console that one row in `topic` changed. **Does not commit.**

    A notice, not a payload: the browser re-reads the row rather than trusting
    a copy of it, so a frame can be lost or reordered and the worst case is a
    moment of staleness. It goes through `publish`, so it inherits the property
    that makes calling it mid-transaction safe: it lands at COMMIT and vanishes
    on ROLLBACK. A console is never told about a transfer it cannot yet read.
    """
    frame = Event(
        topic=topic,
        id=str(subject_id),
        transfer_id=str(transfer_id) if transfer_id is not None else None,
    ).model_dump(by_alias=True)
    await publish(session, EVENTS_CHANNEL, frame)
