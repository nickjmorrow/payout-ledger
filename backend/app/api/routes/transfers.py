"""Disbursement endpoints. Thin: validate, authorize, call a service, respond."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.api.deps import CurrentUser, DbSession
from app.api.idempotent import claim_or_replay
from app.api.middleware import current_request_id
from app.api.schemas import ApiResponse, TransferDetailOut, TransferIn, TransferOut
from app.logging import get_logger
from app.services import idempotency_service, ledger_service, task_service, transfer_service

logger = get_logger(__name__)

router = APIRouter(prefix="/transfers", tags=["transfers"])

ENDPOINT = "POST /transfers"


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: TransferIn,
    session: DbSession,
    response: Response,
    _user: CurrentUser,
    # Long enough not to collide by accident.
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8)],
) -> ApiResponse[TransferOut]:
    """Authorize a disbursement and queue it to be sent, in one transaction.

    `Idempotency-Key` is required: see AGENTS.md > Idempotency.
    """
    payload: dict[str, Any] = body.model_dump(mode="json", by_alias=True)

    replay = await claim_or_replay(
        session, key=idempotency_key, endpoint=ENDPOINT, body=payload, response=response
    )
    if replay is not None:
        # The stored response, not one re-derived from the transfer: identical
        # requests must get identical answers.
        return ApiResponse(data=TransferOut.model_validate(replay.body))

    try:
        transfer = await transfer_service.initiate(
            session,
            recipient_id=body.recipient_id,
            amount_minor=body.amount_minor,
            currency=body.currency,
            request_id=current_request_id(),
        )
    except transfer_service.UnknownRecipientError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except transfer_service.InsufficientFundsError as exc:
        # 422, not 402: the program fund is short, not the caller.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    out = TransferOut.from_transfer(transfer)

    await idempotency_service.record_response(
        session,
        key=idempotency_key,
        status=status.HTTP_201_CREATED,
        body=out.model_dump(mode="json", by_alias=True),
        transfer_id=transfer.id,
    )

    # The one commit. Everything above is in it.
    await session.commit()
    return ApiResponse(data=out)


@router.get("")
async def list_transfers(
    session: DbSession, _user: CurrentUser, run_id: uuid.UUID | None = None
) -> ApiResponse[list[TransferOut]]:
    """The newest transfers; `?run_id=` for the ones in one payment run."""
    transfers = await transfer_service.recent(session, run_id=run_id)
    return ApiResponse(data=[TransferOut.from_transfer(t) for t in transfers])


@router.get("/{transfer_id}")
async def get_transfer(
    transfer_id: uuid.UUID, session: DbSession, _user: CurrentUser
) -> ApiResponse[TransferDetailOut]:
    """One transfer, with its journals and its worker tasks."""
    transfer = await transfer_service.get(session, transfer_id=transfer_id)
    if transfer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such transfer.")

    journals = await ledger_service.journals_for_transfer(session, transfer_id=transfer_id)
    tasks = await task_service.for_transfer(session, transfer_id=transfer_id)
    return ApiResponse(data=TransferDetailOut.from_parts(transfer, journals, tasks))
