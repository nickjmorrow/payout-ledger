"""The camelCase convention every shape on the wire inherits.

**The wire is camelCase; Python is snake_case.** `ApiSchema` does the
conversion at the boundary, so no TypeScript file ever contains `created_at`
and no Python file ever contains `createdAt`.

This is at the top level rather than under `api/` because shapes defined here
are sent over **two transports**: the HTTP responses `api/routes/` returns, and
the frames the worker publishes onto the bus while work is still running. One
definition is what lets a client fold a fetched record and a live one with the
same function; two definitions is a bug that only appears after a refresh. A
process that serves no HTTP should not import the HTTP layer to say what a
frame looks like.

`api/schemas.py` is what is left once that is taken out: the response envelope,
the request bodies, and the resource shapes that genuinely only travel over
HTTP. It imports from here; nothing imports back.

The one frame streamed today is `Event`, below: a change notice, not a
payload. It says *which family of rows* moved and which row, and nothing about
what it now contains — the browser re-reads that from the API, so a lost frame
costs a moment of staleness and never a wrong number. That is the property
`bus.py` asks of everything on the live path.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# The families a change can belong to. Each is a set of queries the console
# holds, and the browser maps a topic to the queries it invalidates.
# `frontend/src/events.ts` lists the same values; a structural test keeps the
# two in step, because a topic the browser has not heard of is a change it
# silently never shows.
Topic = Literal["transfers", "tasks", "findings"]


class Event(ApiSchema):
    """Something in `topic` changed. Re-read it.

    `transfer_id` is set when the thing that changed belongs to a transfer
    without being one: a task sending or checking a payment. It lets the
    browser refresh that transfer's open history rather than every one.
    """

    topic: Topic
    id: str
    transfer_id: str | None = None
