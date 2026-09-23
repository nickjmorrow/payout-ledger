"""What an operator needs to see: balances, people, drift, and stuck work.

These are read-only views onto state the rest of the system already keeps.
Nothing here computes anything the books do not already say — the balances come
from the entries, the findings from the reconciliation pass, the dead letters
from the queue.
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import (
    AccountOut,
    ApiResponse,
    FindingOut,
    OverviewOut,
    QueueOut,
    RecipientOut,
    TaskOut,
)
from app.services import (
    dead_letter_service,
    ledger_service,
    recipient_service,
    reconciliation_service,
    task_service,
)

router = APIRouter(tags=["operations"])


@router.get("/overview")
async def overview(session: DbSession, _user: CurrentUser) -> ApiResponse[OverviewOut]:
    """One request for the console's header.

    Deliberately one endpoint rather than three, because these three numbers
    are read together and a page that fetched them separately could render a
    fund balance from one moment beside a float balance from another — which
    for a ledger reads as the books not adding up.
    """
    accounts = await _system_accounts(session)
    findings = await reconciliation_service.recent_findings(session)
    queue = await task_service.counts(session)

    return ApiResponse(
        data=OverviewOut(
            accounts=accounts,
            # The number that says whether the books are sound. Surfaced rather
            # than buried in a test, because a non-zero value means a trigger
            # has gone missing and the only way anyone finds out is by looking.
            trial_balance_minor=await ledger_service.trial_balance(session),
            unresolved_findings=sum(1 for f in findings if not f.healed),
            # A count, not the length of the dead-letter listing, which is
            # capped: past fifty it would quietly stop going up.
            dead_lettered=queue.dead,
        )
    )


@router.get("/accounts")
async def list_accounts(session: DbSession, _user: CurrentUser) -> ApiResponse[list[AccountOut]]:
    return ApiResponse(data=await _system_accounts(session))


@router.get("/recipients")
async def list_recipients(
    session: DbSession, _user: CurrentUser
) -> ApiResponse[list[RecipientOut]]:
    recipients = await recipient_service.all_recipients(session)
    return ApiResponse(data=[RecipientOut.model_validate(r) for r in recipients])


@router.get("/findings")
async def list_findings(session: DbSession, _user: CurrentUser) -> ApiResponse[list[FindingOut]]:
    findings = await reconciliation_service.recent_findings(session)
    return ApiResponse(data=[FindingOut.model_validate(f) for f in findings])


@router.get("/dead-letters")
async def list_dead_letters(session: DbSession, _user: CurrentUser) -> ApiResponse[list[TaskOut]]:
    """Work that exhausted its retries and is parked for a human.

    The transfers behind these have already been reversed — see
    `worker/disburse` — so what is here is the evidence of why, not money that
    is still stuck.
    """
    tasks = await task_service.dead_lettered(session)
    return ApiResponse(data=[TaskOut.from_task(t) for t in tasks])


@router.post("/dead-letters/{task_id}/retry")
async def retry_dead_letter(
    task_id: uuid.UUID, session: DbSession, _user: CurrentUser
) -> ApiResponse[TaskOut]:
    """Put a dead-lettered task back on the queue, if it is still worth running.

    No idempotency key, and none needed: the service refuses a task that is no
    longer dead, so a repeated request is refused rather than doubled. See
    `dead_letter_service` for which tasks are refused and why.
    """
    try:
        task = await dead_letter_service.retry(session, task_id=task_id)
    except dead_letter_service.DeadLetterError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return ApiResponse(data=TaskOut.from_task(task))


@router.get("/queue")
async def queue(session: DbSession, _user: CurrentUser) -> ApiResponse[QueueOut]:
    """What the workers have to do: counts, and the unfinished work itself."""
    counts = await task_service.counts(session)
    active = await task_service.active(session)
    return ApiResponse(
        data=QueueOut(
            due=counts.due,
            scheduled=counts.scheduled,
            running=counts.running,
            dead=counts.dead,
            active=[TaskOut.from_task(t) for t in active],
        )
    )


async def _system_accounts(session: DbSession) -> list[AccountOut]:
    """The fund and the float, each with its balance derived from the entries."""
    return [
        AccountOut(
            id=account.id,
            name=account.name,
            kind=account.kind,
            currency=account.currency,
            balance_minor=await ledger_service.balance(session, account_id=account.id),
        )
        for account in await ledger_service.system_accounts(session)
    ]
