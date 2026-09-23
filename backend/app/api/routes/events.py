"""Change notices, streamed to the console as Server-Sent Events.

A notice, not the data: the browser re-reads what changed. Streams share the
process's one LISTEN connection rather than holding a database session each.
See AGENTS.md > Live updates.
"""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser
from app.bus import EVENTS_CHANNEL, bus

router = APIRouter(tags=["events"])

# Keeps proxies from timing out an idle stream and lets each side detect a
# dead peer. The browser's STALL_MS in frontend/src/api/events.ts depends on
# it: change one, check the other.
HEARTBEAT_SECONDS = 10.0


@router.get("/events")
async def events(_user: CurrentUser) -> StreamingResponse:
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Turn off nginx's response buffering, which would deliver the stream in one lump.
            "X-Accel-Buffering": "no",
        },
    )


async def stream(*, heartbeat_seconds: float = HEARTBEAT_SECONDS) -> AsyncGenerator[str]:
    """The frames of one connection: `ready`, then changes, with heartbeats between.

    `ready` is sent only once the subscription is live, and the browser re-reads
    everything on it, so no change can fall between the refresh and the stream.
    """
    async with bus.subscribe(EVENTS_CHANNEL) as queue:
        yield _frame("ready", {})
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                # An SSE comment line: ignored by parsers, enough to keep the connection alive.
                yield ": heartbeat\n\n"
                continue
            yield _frame("change", frame)


def _frame(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
