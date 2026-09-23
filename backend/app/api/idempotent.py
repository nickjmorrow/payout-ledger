"""The HTTP half of idempotency: four service outcomes as four responses.

Shared by every endpoint that moves money, so the subtle answers are written
once.
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
    """Take the key and return None, or return what the first request returned.

    On a replay the status and `Idempotency-Replayed` header are already set.
    """
    try:
        claimed = await idempotency_service.claim(session, key=key, endpoint=endpoint, body=body)
    except InFlightError as exc:
        # 409, not 425, which is about TLS early data. `Retry-After` goes on the
        # exception: FastAPI discards the injected response when a handler raises.
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
