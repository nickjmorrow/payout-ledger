"""Payment runs: many disbursements authorized as one decision.

All or nothing, because the whole run is one transaction, and the fund is
checked for the run's total before anything is posted. See AGENTS.md >
Payment runs.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import PaymentRun, Transfer
from app.money import format_money
from app.services import ledger_service, transfer_service

logger = get_logger(__name__)


class RunError(Exception):
    """Base for every refusal in this module."""


class EmptyRunError(RunError):
    """A run with nobody in it."""


class DuplicateRecipientError(RunError):
    """The same person twice in one run, which is almost always a mistake in the list."""


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
    """Authorize every transfer in a run, or none. **Does not commit.**"""
    if not items:
        raise EmptyRunError("A payment run needs at least one recipient.")

    seen: set[uuid.UUID] = set()
    for item in items:
        if item.recipient_id in seen:
            raise DuplicateRecipientError("A recipient appears more than once in this run.")
        seen.add(item.recipient_id)

    total = sum(item.amount_minor for item in items)
    funding = await ledger_service.system_account(
        session, kind="program_funding", currency=currency
    )
    # The lock `initiate` takes, taken first so the total and every transfer are
    # decided against one balance. Row locks are re-entrant within a transaction.
    await ledger_service.lock_account(session, account_id=funding.id)
    available = await ledger_service.balance(session, account_id=funding.id)
    if available < total:
        raise transfer_service.InsufficientFundsError(
            f"This run totals {format_money(total, currency)} across {len(items)} recipients, "
            f"and the program fund holds {format_money(available, currency)}."
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
    return await _summarize(session, runs)


async def summary(session: AsyncSession, *, run_id: uuid.UUID) -> RunSummary | None:
    run = await session.get(PaymentRun, run_id)
    if run is None:
        return None
    return (await _summarize(session, [run]))[0]


async def _summarize(session: AsyncSession, runs: list[PaymentRun]) -> list[RunSummary]:
    """Count each run's transfers by status with one GROUP BY for all of them."""
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
