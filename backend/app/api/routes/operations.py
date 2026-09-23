"""Read-only views for operators: balances, recipients, findings, and the queue."""

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
    """The console's summary figures, in one request so they are from one moment."""
    accounts = await _system_accounts(session)
    queue = await task_service.counts(session)

    return ApiResponse(
        data=OverviewOut(
            accounts=accounts,
            # Always zero while the ledger triggers are in place.
            trial_balance_minor=await ledger_service.trial_balance(session),
            unresolved_findings=await reconciliation_service.currently_reported(session),
            # A count, not the length of the capped listing.
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
    """Work that exhausted its retries. See AGENTS.md > Retries, and the dead-letter queue."""
    tasks = await task_service.dead_lettered(session)
    return ApiResponse(data=[TaskOut.from_task(t) for t in tasks])


@router.post("/dead-letters/{task_id}/retry")
async def retry_dead_letter(
    task_id: uuid.UUID, session: DbSession, _user: CurrentUser
) -> ApiResponse[TaskOut]:
    """Put a dead-lettered task back on the queue, if it is still worth running.

    No idempotency key needed: a repeat is refused, not doubled.
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
