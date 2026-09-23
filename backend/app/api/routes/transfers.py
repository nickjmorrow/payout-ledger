"""Disbursement endpoints.

Thin, like every route here: validate, authorize, call a service, respond. The
only thing with any judgement in it is the idempotency dance in `create`, which
lives in `api/idempotent.py` rather than in a service because it is about HTTP —
it turns four service outcomes into four status codes, and the stored response
it replays is a response.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.api.deps import CurrentUser, DbSession
from app.api.idempotent import claim_or_replay
from app.api.middleware import current_request_id
from app.api.schemas import (
    ApiResponse,
    JournalOut,
    LineOut,
    TaskOut,
    TransferDetailOut,
    TransferIn,
    TransferOut,
)
from app.logging import get_logger
from app.models import Transfer
from app.services import idempotency_service, ledger_service, task_service, transfer_service

logger = get_logger(__name__)

router = APIRouter(prefix="/transfers", tags=["transfers"])

ENDPOINT = "POST /transfers"


def _out(transfer: Transfer) -> TransferOut:
    return TransferOut(
        id=transfer.id,
        recipient_id=transfer.recipient_id,
        recipient_name=transfer.recipient.full_name,
        amount_minor=transfer.amount_minor,
        currency=transfer.currency,
        status=transfer.status,  # pyright: ignore[reportArgumentType]
        provider_reference=transfer.provider_reference,
        failure_reason=transfer.failure_reason,
        run_id=transfer.run_id,
        created_at=transfer.created_at,
        updated_at=transfer.updated_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: TransferIn,
    session: DbSession,
    response: Response,
    _user: CurrentUser,
    # min_length because a key short enough to collide by accident is worse
    # than no key: two unrelated requests would silently become one, and the
    # second caller would be handed a payment it never made.
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8)],
) -> ApiResponse[TransferOut]:
    """Authorise a disbursement and queue it to be sent.

    **The key is required, not optional.** An endpoint that moves money and
    accepts a request without one is an endpoint that will eventually pay
    somebody twice — the caller cannot retry safely and has no way to find out
    whether its first attempt landed. Making it a required header pushes that
    problem to the one place that can solve it: the client, which is the only
    party that knows two requests are the same request.

    Everything below happens in one transaction. The transfer, the journal, the
    idempotency record and the queued payment all commit together or not at
    all.
    """
    payload: dict[str, Any] = body.model_dump(mode="json", by_alias=True)

    replay = await claim_or_replay(
        session, key=idempotency_key, endpoint=ENDPOINT, body=payload, response=response
    )
    if replay is not None:
        # Not re-derived from the transfer row: what the caller gets back must
        # be what the first request actually returned, even if the transfer has
        # moved on since. A replay that reported the *current* status would
        # make two identical requests give two different answers, which is the
        # one thing idempotency is supposed to rule out.
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
        # 422 and not 402 Payment Required: 402 is about the *caller* owing
        # money, which is not what happened. The programme fund is short.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    await session.refresh(transfer, ["recipient"])
    out = _out(transfer)

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
    for transfer in transfers:
        await session.refresh(transfer, ["recipient"])
    return ApiResponse(data=[_out(t) for t in transfers])


@router.get("/{transfer_id}")
async def get_transfer(
    transfer_id: uuid.UUID, session: DbSession, _user: CurrentUser
) -> ApiResponse[TransferDetailOut]:
    """One transfer, with everything that happened to it.

    The journal entries are the books' account of it and the tasks are the
    worker's; between them a person can see the fund debited at authorisation,
    the send and each settlement check, and the reversal if it failed — with
    both the promise and its withdrawal still on the page.
    """
    transfer = await transfer_service.get(session, transfer_id=transfer_id)
    if transfer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such transfer")
    await session.refresh(transfer, ["recipient"])

    journals = await ledger_service.journals_for_transfer(session, transfer_id=transfer_id)
    tasks = await task_service.for_transfer(session, transfer_id=transfer_id)

    return ApiResponse(
        data=TransferDetailOut(
            **_out(transfer).model_dump(),
            journals=[
                JournalOut(
                    id=journal.id,
                    kind=journal.kind,
                    memo=journal.memo,
                    created_at=journal.created_at,
                    lines=[
                        LineOut(
                            account_id=line.account_id,
                            account_kind=line.account.kind,
                            account_name=line.account.name,
                            direction=line.direction,  # pyright: ignore[reportArgumentType]
                            amount_minor=line.amount_minor,
                            currency=line.currency,
                        )
                        for line in journal.lines
                    ],
                )
                for journal in journals
            ],
            tasks=[TaskOut.from_task(task) for task in tasks],
        )
    )
