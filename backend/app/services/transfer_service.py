"""Disbursing money to one recipient: the lifecycle above the books.

Authorizing debits the fund before any money moves, so the same dollar cannot
be promised twice; reversal returns it. See AGENTS.md > The transfer lifecycle
for each status and the journal it posts.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bus import announce
from app.logging import get_logger
from app.models import Recipient, Transfer
from app.money import format_money
from app.services import ledger_service, task_service
from app.services.ledger_service import Posting

logger = get_logger(__name__)

# Registered in worker/disburse.py.
DISBURSE = "disburse_transfer"

# How long after sending to ask the provider what became of a payment.
SETTLE_CHECK_DELAY_SECONDS = 3


class TransferError(Exception):
    """Base for every refusal in this module."""


class UnknownRecipientError(TransferError):
    """No such recipient."""


class InsufficientFundsError(TransferError):
    """The program fund does not hold enough to authorize this."""


async def initiate(
    session: AsyncSession,
    *,
    recipient_id: uuid.UUID,
    amount_minor: int,
    currency: str,
    request_id: str | None = None,
    run_id: uuid.UUID | None = None,
) -> Transfer:
    """Authorize a disbursement and queue it to be sent. Does not commit.

    The transfer, its journal and the task that sends it land together in the
    caller's transaction.
    """
    recipient = await session.get(Recipient, recipient_id)
    if recipient is None:
        raise UnknownRecipientError("No such recipient.")

    funding = await ledger_service.system_account(
        session, kind="program_funding", currency=currency
    )

    # Before the balance is read, or two concurrent transfers can overdraw it.
    await ledger_service.lock_account(session, account_id=funding.id)

    available = await ledger_service.balance(session, account_id=funding.id)
    if available < amount_minor:
        raise InsufficientFundsError(
            f"The program fund holds {format_money(available, currency)}, "
            f"and this payment is {format_money(amount_minor, currency)}."
        )

    payable = await ledger_service.payable_account(
        session, recipient_id=recipient_id, currency=currency
    )

    transfer = Transfer(
        recipient=recipient,
        recipient_id=recipient_id,
        amount_minor=amount_minor,
        currency=currency,
        status="pending",
        run_id=run_id,
    )
    session.add(transfer)
    await session.flush()

    await ledger_service.post(
        session,
        kind="transfer_authorized",
        currency=currency,
        transfer_id=transfer.id,
        memo=f"Authorized for {recipient.full_name}",
        postings=[
            Posting(account_id=funding.id, direction="debit", amount_minor=amount_minor),
            Posting(account_id=payable.id, direction="credit", amount_minor=amount_minor),
        ],
    )

    await task_service.enqueue(
        session,
        kind=DISBURSE,
        payload={"transfer_id": str(transfer.id)},
        request_id=request_id,
    )
    await announce(session, topic="transfers", subject_id=transfer.id)

    logger.info(
        "transfer initiated",
        transfer_id=str(transfer.id),
        recipient_id=str(recipient_id),
        amount_minor=amount_minor,
        currency=currency,
    )
    return transfer


async def mark_processing(
    session: AsyncSession, *, transfer: Transfer, provider_reference: str
) -> None:
    """The provider has accepted it. No posting: the books already say enough."""
    transfer.status = "processing"
    transfer.provider_reference = provider_reference
    await session.flush()
    await announce(session, topic="transfers", subject_id=transfer.id)


async def mark_succeeded(session: AsyncSession, *, transfer: Transfer) -> None:
    """The recipient was paid: clear the payable and spend the float."""
    if transfer.status == "succeeded":
        return

    payable = await ledger_service.payable_account(
        session, recipient_id=transfer.recipient_id, currency=transfer.currency
    )
    settlement = await ledger_service.system_account(
        session, kind="provider_settlement", currency=transfer.currency
    )

    await ledger_service.post(
        session,
        kind="transfer_settled",
        currency=transfer.currency,
        transfer_id=transfer.id,
        postings=[
            Posting(account_id=payable.id, direction="debit", amount_minor=transfer.amount_minor),
            Posting(
                account_id=settlement.id, direction="credit", amount_minor=transfer.amount_minor
            ),
        ],
    )
    transfer.status = "succeeded"
    await session.flush()
    await announce(session, topic="transfers", subject_id=transfer.id)
    logger.info("transfer succeeded", transfer_id=str(transfer.id))


async def mark_failed(session: AsyncSession, *, transfer: Transfer, reason: str) -> None:
    """It will not happen: post a reversing journal and return the money to the fund."""
    if transfer.status == "failed":
        return

    payable = await ledger_service.payable_account(
        session, recipient_id=transfer.recipient_id, currency=transfer.currency
    )
    funding = await ledger_service.system_account(
        session, kind="program_funding", currency=transfer.currency
    )

    await ledger_service.post(
        session,
        kind="transfer_reversed",
        currency=transfer.currency,
        transfer_id=transfer.id,
        memo=reason,
        postings=[
            Posting(account_id=payable.id, direction="debit", amount_minor=transfer.amount_minor),
            Posting(account_id=funding.id, direction="credit", amount_minor=transfer.amount_minor),
        ],
    )
    transfer.status = "failed"
    transfer.failure_reason = reason
    await session.flush()
    await announce(session, topic="transfers", subject_id=transfer.id)
    logger.info("transfer failed", transfer_id=str(transfer.id), reason=reason)


async def get(session: AsyncSession, *, transfer_id: uuid.UUID) -> Transfer | None:
    """One transfer with its recipient, loaded in the same round trip."""
    result = await session.execute(
        select(Transfer).options(selectinload(Transfer.recipient)).where(Transfer.id == transfer_id)
    )
    return result.scalar_one_or_none()


async def recent(
    session: AsyncSession, *, limit: int = 50, run_id: uuid.UUID | None = None
) -> list[Transfer]:
    """The newest transfers, or the newest in one run."""
    # Recipients in one extra query for the page, not one per row.
    query = (
        select(Transfer)
        .options(selectinload(Transfer.recipient))
        .order_by(Transfer.created_at.desc())
        .limit(limit)
    )
    if run_id is not None:
        query = query.where(Transfer.run_id == run_id)
    result = await session.execute(query)
    return list(result.scalars())
