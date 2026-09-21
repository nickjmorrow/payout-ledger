"""What an operator needs to see: balances, people, drift, and stuck work.

These are read-only views onto state the rest of the system already keeps.
Nothing here computes anything the books do not already say — the balances come
from the entries, the findings from the reconciliation pass, the dead letters
from the queue.
"""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import (
    AccountOut,
    ApiResponse,
    FindingOut,
    OverviewOut,
    RecipientOut,
    TaskOut,
)
from app.services import (
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
    dead = await task_service.dead_lettered(session)

    return ApiResponse(
        data=OverviewOut(
            accounts=accounts,
            # The number that says whether the books are sound. Surfaced rather
            # than buried in a test, because a non-zero value means a trigger
            # has gone missing and the only way anyone finds out is by looking.
            trial_balance_minor=await ledger_service.trial_balance(session),
            unresolved_findings=sum(1 for f in findings if not f.healed),
            dead_lettered=len(dead),
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
