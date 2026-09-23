"""Payment runs: authorize every transfer in a run or none. Requires an Idempotency-Key."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.api.deps import CurrentUser, DbSession
from app.api.idempotent import claim_or_replay
from app.api.middleware import current_request_id
from app.api.schemas import ApiResponse, RunIn, RunOut
from app.services import idempotency_service, run_service, transfer_service
from app.services.run_service import RunItem

router = APIRouter(prefix="/runs", tags=["runs"])

ENDPOINT = "POST /runs"


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: RunIn,
    session: DbSession,
    response: Response,
    _user: CurrentUser,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8)],
) -> ApiResponse[RunOut]:
    """Authorize every transfer in a run and queue them to be sent, or none of them."""
    payload: dict[str, Any] = body.model_dump(mode="json", by_alias=True)
    replay = await claim_or_replay(
        session, key=idempotency_key, endpoint=ENDPOINT, body=payload, response=response
    )
    if replay is not None:
        return ApiResponse(data=RunOut.model_validate(replay.body))

    try:
        run = await run_service.initiate(
            session,
            items=[RunItem(i.recipient_id, i.amount_minor) for i in body.items],
            currency=body.currency,
            memo=body.memo,
            request_id=current_request_id(),
        )
    except transfer_service.UnknownRecipientError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (
        transfer_service.InsufficientFundsError,
        run_service.DuplicateRecipientError,
        run_service.EmptyRunError,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    summary = await run_service.summary(session, run_id=run.id)
    if summary is None:  # pragma: no cover - the run was flushed just above
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "The run was not found after it was created."
        )
    out = RunOut.from_summary(summary)

    await idempotency_service.record_response(
        session,
        key=idempotency_key,
        status=status.HTTP_201_CREATED,
        body=out.model_dump(mode="json", by_alias=True),
    )

    # The one commit: everything above lands together.
    await session.commit()
    return ApiResponse(data=out)


@router.get("")
async def list_runs(session: DbSession, _user: CurrentUser) -> ApiResponse[list[RunOut]]:
    return ApiResponse(data=[RunOut.from_summary(s) for s in await run_service.recent(session)])
