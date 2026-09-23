"""Change notices between processes, over Postgres LISTEN/NOTIFY.

Durable state lives in tables; this channel carries only notices a viewer could
recover by re-reading them, so a lost frame costs a moment of staleness. NOTIFY
payloads are capped at 8000 bytes. See AGENTS.md > Live updates.
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

# Change notices for the console. Separate from the worker's wake-up channel
# in task_service, which has different listeners.
EVENTS_CHANNEL = "ledger_events"

# Bounded, so one stalled browser cannot grow a queue without limit.
SUBSCRIBER_QUEUE_SIZE = 512


class Bus:
    """One LISTEN connection per process, fanned out to subscribers in memory."""

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
        # Called by asyncpg on the event loop. Must not block or raise: the driver
        # swallows exceptions and the subscriber would silently stop hearing.
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
    """Send one frame to everyone listening on `channel`. Does not commit.

    NOTIFY is transactional: it is delivered at the caller's COMMIT and discarded
    on ROLLBACK, so a notice and the rows it describes land together.
    """
    await session.execute(
        text("select pg_notify(:channel, :payload)"),
        {"channel": channel, "payload": json.dumps(frame)},
    )


# One per process, started by main.py's lifespan and by the worker loop.
bus = Bus()


async def announce(
    session: AsyncSession,
    *,
    topic: Topic,
    subject_id: UUID,
    transfer_id: UUID | str | None = None,
) -> None:
    """Tell every open console that one row in `topic` changed. Does not commit."""
    frame = Event(
        topic=topic,
        id=str(subject_id),
        transfer_id=str(transfer_id) if transfer_id is not None else None,
    ).model_dump(by_alias=True)
    await publish(session, EVENTS_CHANNEL, frame)
