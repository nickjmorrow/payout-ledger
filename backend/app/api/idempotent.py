"""The HTTP half of idempotency: four service outcomes, four responses.

Every endpoint that moves money requires an `Idempotency-Key`, and each of them
has to turn `idempotency_service.claim` into the same four answers. Written once
here, because the two that are not "proceed" are the subtle ones — a 409 that
says *ask again* rather than guessing, and a 422 for a key reused with a
different body — and a second hand-written copy is where one of them would
quietly become a 200.

HTTP rather than business logic, which is why it is in `api/` and not
`services/`: it sets status codes and headers, and what it replays is a
response.
"""

from typing import Any

from fastapi import HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import idempotency_service
from app.services.idempotency_service import InFlightError, KeyConflictError, Replay


async def claim_or_replay(
    session: AsyncSession,
    *,
    key: str,
    endpoint: str,
    body: dict[str, Any],
    response: Response,
) -> Replay | None:
    """Take the key and return None — or return what the first request returned.

    On a replay the response's status and `Idempotency-Replayed` header are
    already set; the caller returns the stored body as it is. Not re-derived
    from the current state of the thing it describes: two identical requests
    must give the same answer even if the world has moved on since.
    """
    try:
        claimed = await idempotency_service.claim(session, key=key, endpoint=endpoint, body=body)
    except InFlightError as exc:
        # 409 rather than 425 Too Early: 425 is specific to replayed TLS early
        # data and means something else. `Retry-After` because the wait is
        # bounded by the other request's transaction, not open-ended.
        #
        # On the exception, not on `response`. FastAPI discards the injected
        # response when a handler raises and builds a fresh one from the
        # exception, so a header set there never reaches the client — which is
        # where this one was until a test asked for it.
        raise HTTPException(
            status.HTTP_409_CONFLICT, str(exc), headers={"Retry-After": "1"}
        ) from exc
    except KeyConflictError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if isinstance(claimed, Replay):
        response.status_code = claimed.status
        response.headers["Idempotency-Replayed"] = "true"
        return claimed
    return None
