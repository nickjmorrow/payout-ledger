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

Today this module holds only the base class, because nothing is streamed yet.
The event vocabulary that used to live here was the chat transcript's and left
with it. When the first thing worth watching live arrives, its frame shapes
belong here rather than in `api/schemas.py`.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
