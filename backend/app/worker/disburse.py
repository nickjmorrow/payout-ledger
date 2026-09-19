"""Sending a payment, and finding out what happened to it.

Two task kinds, because instructing a payment and learning its outcome are
genuinely different jobs with different failure modes:

  `disburse_transfer`  hand the payment to the provider. Fails when they cannot
                       be reached, which is usually worth retrying.
  `settle_transfer`    ask what became of it. Fails only if they cannot be
                       reached; a payment that is still in flight is not a
                       failure, it is an answer, and the job reschedules itself.

**The provider's idempotency key is the transfer id.** Stable across every
retry, so however many times this runs, the provider records one payment. That
is what makes the whole at-least-once design safe: the worker may send twice,
the recipient is paid once.

The most important lines in this file are the three `already` checks. A worker
killed after the provider accepted a payment but before the row was updated
will run again from the top, and the only thing standing between that and a
second disbursement is asking before acting.
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
    """The transfer this task is about, with its recipient already loaded.

    Eagerly, because `send_payment` needs the recipient's number and a lazy
    relationship cannot emit IO from inside async code — it raises
    MissingGreenlet, which reads like a driver bug rather than a missing
    `selectinload`.
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
        # Not retryable: the payload is wrong or the transfer is gone, and
        # neither gets better by trying again.
        return TaskOutcome("failed", error=f"no transfer for task payload {task.payload!r}")

    # Already sent. A worker killed between the provider accepting and this row
    # being updated comes back here, and without this check it would send a
    # second payment. The provider would dedupe it — that is what the key is
    # for — but relying on the other side to catch our mistake is not a design.
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

    # The check runs later, not now: the provider has only just accepted it and
    # asking immediately would always get `pending`. `run_at` is the queue's
    # own scheduling, so this costs no timer and no extra process.
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

    Polling rather than waiting for a webhook. A webhook is an optimisation on
    top of this, never a replacement: a callback that is never delivered leaves
    a payment in flight forever, and the only thing that finds it is somebody
    asking.
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
        # They have no record of a payment we believe we sent. That is real
        # drift rather than a transient fault, and guessing either way here
        # would either lose money or pay twice — so it is left for
        # reconciliation, which is the job that exists to decide.
        return TaskOutcome(
            "failed",
            error=f"provider has no record of {transfer.provider_reference}",
        )

    if payment.status == "pending":
        # Not a failure. Ask again later, and keep doing so — the retry budget
        # is for errors, and spending it on a payment that is merely slow would
        # abandon a transfer that is going to settle perfectly well.
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
    """Decide what a failed send means for the transfer, not just for the task.

    **This is where the dead-letter queue stops being a leak.** A task that
    exhausts its retries is parked as `failed` for a human to look at — but if
    nothing else happened, the transfer would sit at `pending` forever with the
    fund still debited, money promised to somebody who will never receive it.

    So the last attempt reverses the transfer before giving up. `attempts` was
    incremented by the claim, so it already counts this one.
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
        # Not retryable now whatever the provider said: the transfer has been
        # reversed, so a later attempt would be acting on a decision already
        # made and would find the transfer no longer `pending`.
        return TaskOutcome("failed", error=str(exc), retryable=False)

    return TaskOutcome("failed", error=str(exc), retryable=True)
