"""Sending a payment, and finding out what happened to it.

`disburse_transfer` hands the payment to the provider; `settle_transfer` asks
what became of it. The provider's idempotency key is the transfer id, so a
repeated send is one payment. Each handler checks the transfer's state before
acting. See AGENTS.md > Checking before acting.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging import get_logger
from app.models import Task, Transfer
from app.provider.base import PaymentProvider, ProviderError, ProviderPaymentView
from app.provider.registry import get_provider
from app.services import task_service, transfer_service
from app.worker.handlers import TaskOutcome, register

logger = get_logger(__name__)

SETTLE = "settle_transfer"


def _provider() -> PaymentProvider:
    """Indirection so a test can substitute a fake without patching an import."""
    return get_provider()


async def _load(session: AsyncSession, task: Task) -> Transfer | None:
    """The transfer this task is about, with its recipient loaded eagerly.

    Eagerly, because a lazy load from async code raises MissingGreenlet.
    """
    raw = task.payload.get("transfer_id")
    if raw is None:
        return None

    result = await session.execute(
        select(Transfer)
        .options(selectinload(Transfer.recipient))
        .where(Transfer.id == uuid.UUID(raw))
    )
    return result.scalar_one_or_none()


@register("disburse_transfer")
async def disburse(session: AsyncSession, task: Task) -> TaskOutcome:
    """Hand the payment to the provider, then schedule a check on it."""
    transfer = await _load(session, task)
    if transfer is None:
        # Not retryable: a bad payload does not get better.
        return TaskOutcome("failed", error=f"no transfer for task payload {task.payload!r}")

    # Already handed over, by a run that died before recording it. The provider
    # would dedupe a repeat, but we should not rely on that.
    if transfer.status != "pending":
        logger.info(
            "transfer already handed over, skipping",
            transfer_id=str(transfer.id),
            status=transfer.status,
        )
        return TaskOutcome("succeeded")

    try:
        payment = await _provider().send_payment(
            # The transfer id, so every retry of this task is one payment.
            idempotency_key=str(transfer.id),
            msisdn=transfer.recipient.msisdn,
            amount_minor=transfer.amount_minor,
            currency=transfer.currency,
        )
    except ProviderError as exc:
        return await _handle_provider_error(session, task=task, transfer=transfer, exc=exc)

    await transfer_service.mark_processing(
        session, transfer=transfer, provider_reference=payment.reference
    )

    # Check later: asking now would always find it pending.
    await task_service.enqueue(
        session,
        kind=SETTLE,
        payload={"transfer_id": str(transfer.id)},
        run_at=task_service.seconds_from_now(transfer_service.SETTLE_CHECK_DELAY_SECONDS),
        request_id=task.request_id,
    )
    await session.commit()
    return TaskOutcome("succeeded")


@register(SETTLE)
async def settle(session: AsyncSession, task: Task) -> TaskOutcome:
    """Ask the provider what happened, and write it down.

    Polling, not a webhook: a callback that never arrives leaves a payment in
    flight forever. See AGENTS.md > Asking, not waiting.
    """
    transfer = await _load(session, task)
    if transfer is None:
        return TaskOutcome("failed", error=f"no transfer for task payload {task.payload!r}")

    if transfer.status in {"succeeded", "failed"}:
        return TaskOutcome("succeeded")

    if transfer.provider_reference is None:
        return TaskOutcome(
            "failed", error=f"transfer {transfer.id} is {transfer.status} with no reference"
        )

    try:
        payment = await _provider().get_payment(reference=transfer.provider_reference)
    except ProviderError as exc:
        return TaskOutcome("failed", error=str(exc), retryable=exc.retryable)

    return await _apply(session, task=task, transfer=transfer, payment=payment)


async def _apply(
    session: AsyncSession,
    *,
    task: Task,
    transfer: Transfer,
    payment: ProviderPaymentView | None,
) -> TaskOutcome:
    """Turn the provider's answer into a decision about the transfer."""
    if payment is None:
        # They have no record of a payment we believe we sent: real drift, left for
        # reconciliation to report rather than guessed at here.
        return TaskOutcome(
            "failed",
            error=f"provider has no record of {transfer.provider_reference}",
        )

    if payment.status == "pending":
        # Slow is not failed: ask again later without spending the retry budget.
        await task_service.enqueue(
            session,
            kind=SETTLE,
            payload={"transfer_id": str(transfer.id)},
            run_at=task_service.seconds_from_now(transfer_service.SETTLE_CHECK_DELAY_SECONDS),
            request_id=task.request_id,
        )
        await session.commit()
        return TaskOutcome("succeeded")

    if payment.status == "succeeded":
        await transfer_service.mark_succeeded(session, transfer=transfer)
    else:
        await transfer_service.mark_failed(
            session, transfer=transfer, reason=payment.failure_reason or "provider reported failure"
        )
    await session.commit()
    return TaskOutcome("succeeded")


async def _handle_provider_error(
    session: AsyncSession, *, task: Task, transfer: Transfer, exc: ProviderError
) -> TaskOutcome:
    """Decide what a failed send means for the transfer, not just the task.

    The last attempt reverses the transfer before the task is dead-lettered, so the
    fund is not left debited. `attempts` already counts this attempt.
    """
    is_last = task.attempts >= task.max_attempts
    if not exc.retryable or is_last:
        await transfer_service.mark_failed(session, transfer=transfer, reason=str(exc))
        await session.commit()
        logger.warning(
            "transfer reversed after provider failure",
            transfer_id=str(transfer.id),
            attempts=task.attempts,
            retryable=exc.retryable,
        )
        # The transfer is reversed now, so another attempt could only find it finished.
        return TaskOutcome("failed", error=str(exc), retryable=False)

    return TaskOutcome("failed", error=str(exc), retryable=True)
