"""Payment runs: many disbursements, authorised as one decision.

**All or nothing.** A run is every one of its transfers or none of them. That
is not a policy layered on top — it falls out of doing the whole run in one
transaction: N calls to `transfer_service.initiate`, each posting its own
journal and enqueueing its own payment, committed together by the caller. A
run that fails on its seventh recipient leaves no trace of the first six,
because they were never visible to anyone.

The alternative, partial runs, sounds kinder and is worse. An operator who
asked to pay forty people and was told "twenty-three authorised, see errors"
now has to work out which seventeen to retry, with a fund that has moved
underneath them, and the chance of paying somebody twice is the chance they
get that list wrong.

**The fund is checked for the whole run before any of it is posted.** Each
`initiate` would refuse the transfer that tipped the fund over anyway, and the
transaction would roll back — correctly, but with an error about one
recipient when the truth is about the total. Checking the sum first, under the
same lock, gives the operator the number they need.

A run's progress and total are derived from its transfers. See `PaymentRun`.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import PaymentRun, Transfer
from app.services import ledger_service, transfer_service

logger = get_logger(__name__)


class RunError(Exception):
    """Base for every refusal in this module."""


class EmptyRunError(RunError):
    """A run with nobody in it."""


class DuplicateRecipientError(RunError):
    """The same person twice in one run.

    Refused rather than merged or allowed. Paying one recipient twice in a
    cycle is almost always a mistake in whatever list the run was built from,
    and the cheapest moment to catch that is before either payment exists.
    """


@dataclass(frozen=True)
class RunItem:
    recipient_id: uuid.UUID
    amount_minor: int


@dataclass
class RunSummary:
    """A run with its progress, counted from its transfers when asked."""

    run: PaymentRun
    count: int = 0
    total_minor: int = 0
    by_status: dict[str, int] = field(default_factory=dict[str, int])


async def initiate(
    session: AsyncSession,
    *,
    items: list[RunItem],
    currency: str,
    memo: str | None = None,
    request_id: str | None = None,
) -> PaymentRun:
    """Authorise every transfer in a run, or none. **Does not commit.**"""
    if not items:
        raise EmptyRunError("a payment run needs at least one recipient")

    seen: set[uuid.UUID] = set()
    for item in items:
        if item.recipient_id in seen:
            raise DuplicateRecipientError(
                f"recipient {item.recipient_id} appears more than once in this run"
            )
        seen.add(item.recipient_id)

    total = sum(item.amount_minor for item in items)
    funding = await ledger_service.system_account(
        session, kind="program_funding", currency=currency
    )
    # The same lock `transfer_service.initiate` takes, taken first so the total
    # and every transfer after it are decided against one balance. Postgres row
    # locks are re-entrant within a transaction, so each `initiate` below
    # re-taking it costs nothing and blocks nothing.
    await ledger_service.lock_account(session, account_id=funding.id)
    available = await ledger_service.balance(session, account_id=funding.id)
    if available < total:
        raise transfer_service.InsufficientFundsError(
            f"this run totals {total} {currency} minor units across {len(items)} recipients; "
            f"the program fund holds {available}"
        )

    run = PaymentRun(memo=memo, currency=currency)
    session.add(run)
    await session.flush()

    for item in items:
        await transfer_service.initiate(
            session,
            recipient_id=item.recipient_id,
            amount_minor=item.amount_minor,
            currency=currency,
            request_id=request_id,
            run_id=run.id,
        )

    logger.info(
        "payment run initiated",
        run_id=str(run.id),
        transfers=len(items),
        total_minor=total,
        currency=currency,
    )
    return run


async def recent(session: AsyncSession, *, limit: int = 20) -> list[RunSummary]:
    """The newest runs, each with its progress counted from its transfers."""
    runs = list(
        (
            await session.execute(
                select(PaymentRun).order_by(PaymentRun.created_at.desc()).limit(limit)
            )
        ).scalars()
    )
    return await _summarise(session, runs)


async def summary(session: AsyncSession, *, run_id: uuid.UUID) -> RunSummary | None:
    run = await session.get(PaymentRun, run_id)
    if run is None:
        return None
    return (await _summarise(session, [run]))[0]


async def _summarise(session: AsyncSession, runs: list[PaymentRun]) -> list[RunSummary]:
    """Count each run's transfers by status, in one query for all of them.

    Two queries rather than one join: the runs, then one GROUP BY over their
    transfers. A join would return a row per transfer and count in Python; this
    counts in Postgres, on `transfers_run_idx`, and returns a row per status.
    """
    summaries = {run.id: RunSummary(run=run) for run in runs}
    if not summaries:
        return []

    counts = await session.execute(
        select(
            Transfer.run_id,
            Transfer.status,
            func.count(),
            func.sum(Transfer.amount_minor),
        )
        .where(Transfer.run_id.in_(summaries))
        .group_by(Transfer.run_id, Transfer.status)
    )
    for run_id, status, count, amount in counts.tuples():
        if run_id is None:
            continue
        summary = summaries[run_id]
        summary.by_status[status] = int(count)
        summary.count += int(count)
        summary.total_minor += int(amount or 0)

    return [summaries[run.id] for run in runs]
