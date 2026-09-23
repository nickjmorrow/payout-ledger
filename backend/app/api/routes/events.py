"""Change notices, streamed to the console as Server-Sent Events.

**A notice, not the data.** Each frame says which family of rows moved — a
transfer, a task, a finding — and which row. The browser then re-reads it
through the ordinary endpoints. So the stream can drop a frame, deliver two
out of order, or disconnect for a minute, and the worst outcome is a moment of
staleness: the authoritative state is always one request away, which is the
property `bus.py` asks of anything on the live path.

**No database session per stream.** Every open console shares the process's
one LISTEN connection through `bus.subscribe`. A stream holding a pooled
connection for as long as a tab is open would exhaust the pool at a handful of
tabs, and nothing here needs one.
"""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser
from app.bus import EVENTS_CHANNEL, bus

router = APIRouter(tags=["events"])

# Three jobs. It beats the idle timeouts of the proxies in front — nginx,
# Caddy, a load balancer. It finds a dead client, since a write to a closed
# socket is how the server learns of one. And it lets a client find a dead
# *server*: `STALL_MS` in `frontend/src/api/events.ts` treats this much silence,
# and then some, as a connection that has quietly gone — which is what a proxy
# does when its upstream dies and it never says so. Change one, check the other.
HEARTBEAT_SECONDS = 10.0


@router.get("/events")
async def events(_user: CurrentUser) -> StreamingResponse:
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx buffers proxied responses by default, which delivers a
            # stream as one lump when it closes. This turns it off per response,
            # on top of `proxy_buffering off` in nginx.conf.
            "X-Accel-Buffering": "no",
        },
    )


async def stream(*, heartbeat_seconds: float = HEARTBEAT_SECONDS) -> AsyncGenerator[str]:
    """The frames of one connection: `ready`, then changes, with heartbeats between.

    **`ready` is sent only after the subscription is live, and the browser
    re-reads everything when it arrives.** That ordering is the whole
    correctness argument for reconnecting. Headers go out before this generator
    first runs, so a client that refreshed on connect — on the socket opening —
    could read the world, then miss a change committed before LISTEN was in
    place. Refreshing on `ready` instead means any change the refresh did not
    see is one the subscription will.
    """
    async with bus.subscribe(EVENTS_CHANNEL) as queue:
        yield _frame("ready", {})
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                # A comment line: ignored by every SSE parser, and enough bytes
                # to keep an idle connection open and to find a dead one.
                yield ": heartbeat\n\n"
                continue
            yield _frame("change", frame)


def _frame(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
