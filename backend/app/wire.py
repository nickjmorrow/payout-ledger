"""Shapes shared by both transports, HTTP and the event stream.

The wire is camelCase and Python is snake_case; `ApiSchema` converts at the
boundary. Here rather than under `api/` so the worker can publish a frame
without importing the HTTP layer.
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


# What a change can be about. Must match TOPICS in frontend/src/events.ts; a
# structural test checks it.
Topic = Literal["transfers", "tasks", "findings"]


class Event(ApiSchema):
    """Something in `topic` changed; re-read it.

    `transfer_id` names the transfer a changed task belongs to, so the browser can
    refresh just that transfer's history.
    """

    topic: Topic
    id: str
    transfer_id: str | None = None
