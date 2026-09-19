"""Disbursing money to a recipient: the domain flow above the books.

A transfer has two lives. The `transfers` row is the *intent* and its progress,
mutable, with a status. The journal entries are what actually happened,
immutable. Keeping them separate is what lets a transfer fail and be retried
without the books ever showing a payment that did not occur.

The states, and what each one means in the ledger:

  pending     authorised. The fund has been debited and the recipient is owed
              the money, but nothing has left the provider. `transfer_authorized`
              is posted.
  processing  handed to the provider, who has accepted it. No new posting — the
              books already say what they need to.
  succeeded   the provider confirms the recipient was paid. `transfer_settled`
              clears the payable and spends the float.
  failed      it will not happen. `transfer_reversed` undoes the authorisation
              and returns the money to the fund.

**Authorising debits the fund immediately, before the money moves.** That is
deliberate and is the conservative direction: the programme should not be able
to promise the same shilling twice while a payment is in flight. The money
comes back on reversal if the payment fails.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Recipient, Transfer
from app.services import ledger_service, task_service
from app.services.ledger_service import Posting

logger = get_logger(__name__)

# The task kind the worker claims to actually send the payment. Registered in
# worker/handlers.py.
DISBURSE = "disburse_transfer"


class TransferError(Exception):
    """Base for every refusal in this module."""


class UnknownRecipientError(TransferError):
    """No such recipient."""


class InsufficientFundsError(TransferError):
    """The programme fund does not hold enough to authorise this."""


async def initiate(
    session: AsyncSession,
    *,
    recipient_id: uuid.UUID,
    amount_minor: int,
    currency: str,
    request_id: str | None = None,
) -> Transfer:
    """Authorise a disbursement and queue it to be sent. **Does not commit.**

    Everything this writes — the transfer, the journal, and the task that will
    send the payment — lands in the caller's transaction, together or not at
    all. That is the outbox pattern, and here it needs no extra machinery
    because the queue is a table: see `task_service.enqueue`.
    """
    recipient = await session.get(Recipient, recipient_id)
    if recipient is None:
        raise UnknownRecipientError(f"no recipient {recipient_id}")

    funding = await ledger_service.system_account(
        session, kind="program_funding", currency=currency
    )

    # Taken *before* the balance is read. Two concurrent transfers that both
    # read first would both see enough and both post, overdrawing a fund while
    # every journal balances perfectly. See `ledger_service.lock_account`.
    await ledger_service.lock_account(session, account_id=funding.id)

    available = await ledger_service.balance(session, account_id=funding.id)
    if available < amount_minor:
        raise InsufficientFundsError(
            f"programme fund holds {available} {currency} minor units; {amount_minor} requested"
        )

    payable = await ledger_service.payable_account(
        session, recipient_id=recipient_id, currency=currency
    )

    transfer = Transfer(
        recipient_id=recipient_id,
        amount_minor=amount_minor,
        currency=currency,
        status="pending",
    )
    session.add(transfer)
    await session.flush()

    await ledger_service.post(
        session,
        kind="transfer_authorized",
        currency=currency,
        transfer_id=transfer.id,
        memo=f"Authorised for {recipient.full_name}",
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
    """The provider has accepted it. No posting — the books already say enough.

    Recording their reference is what makes the transfer findable from their
    side, which is what reconciliation needs and what lets a retry ask "did
    this one actually land" instead of sending it again.
    """
    transfer.status = "processing"
    transfer.provider_reference = provider_reference
    await session.flush()


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
    logger.info("transfer succeeded", transfer_id=str(transfer.id))


async def mark_failed(session: AsyncSession, *, transfer: Transfer, reason: str) -> None:
    """It will not happen: reverse the authorisation and return the money.

    A reversing journal rather than deleting the original. Both the promise and
    its withdrawal stay in the history, which is the entire reason to keep
    books this way — and is what lets someone answer "why did this recipient's
    balance move twice" months later.
    """
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
    logger.info("transfer failed", transfer_id=str(transfer.id), reason=reason)


async def get(session: AsyncSession, *, transfer_id: uuid.UUID) -> Transfer | None:
    return await session.get(Transfer, transfer_id)


async def recent(session: AsyncSession, *, limit: int = 50) -> list[Transfer]:
    result = await session.execute(
        select(Transfer).order_by(Transfer.created_at.desc()).limit(limit)
    )
    return list(result.scalars())
