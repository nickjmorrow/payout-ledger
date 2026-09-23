"""Posting to the books, and reading balances back out.

Every write to `ledger_entries` goes through `post`, whose checks duplicate the
database triggers to give a readable error at the call site; the triggers are
the enforcement. Nothing here commits: posting lands in the caller's
transaction. See AGENTS.md > The books.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging import get_logger
from app.models import Account, JournalEntry, LedgerEntry

logger = get_logger(__name__)

Direction = Literal["debit", "credit"]

JournalKind = Literal[
    "funding_deposit",
    "transfer_authorized",
    "transfer_settled",
    "transfer_reversed",
]

# Which direction increases each kind of account: assets rise on a debit,
# liabilities and equity on a credit. Written down once, so `balance` reads the
# way a person expects and nothing else has to remember.
NATURAL_DIRECTION: dict[str, Direction] = {
    "program_funding": "credit",  # equity
    "recipient_payable": "credit",  # liability
    "provider_settlement": "debit",  # asset
}


class LedgerError(Exception):
    """Base for every refusal in this module."""


class UnbalancedError(LedgerError):
    """Debits and credits do not agree, so this is not a posting."""


# A posting moves money between accounts.
MINIMUM_LINES = 2


@dataclass(frozen=True)
class Posting:
    """One line, before it is a row. `amount_minor` is positive; `direction` is the sign."""

    account_id: uuid.UUID
    direction: Direction
    amount_minor: int


async def post(
    session: AsyncSession,
    *,
    kind: JournalKind,
    postings: list[Posting],
    currency: str,
    transfer_id: uuid.UUID | None = None,
    memo: str | None = None,
) -> JournalEntry:
    """Write one balanced journal entry. Does not commit.

    `currency` belongs to the journal, not each line, so a mixed-currency journal
    cannot be expressed here at all.
    """
    if len(postings) < MINIMUM_LINES:
        raise UnbalancedError(
            f"a {kind} journal needs at least two lines, got {len(postings)}; "
            "a posting moves money between accounts"
        )

    for line in postings:
        if line.amount_minor <= 0:
            raise UnbalancedError(
                f"line for account {line.account_id} has amount {line.amount_minor}; "
                "amounts are always positive and `direction` carries the sign"
            )

    debit_total = sum(p.amount_minor for p in postings if p.direction == "debit")
    credit_total = sum(p.amount_minor for p in postings if p.direction == "credit")
    if debit_total != credit_total:
        raise UnbalancedError(
            f"a {kind} journal does not balance: debits {debit_total} != "
            f"credits {credit_total} (difference {debit_total - credit_total})"
        )

    journal = JournalEntry(kind=kind, transfer_id=transfer_id, memo=memo)
    session.add(journal)
    await session.flush()

    for line in postings:
        session.add(
            LedgerEntry(
                journal_entry_id=journal.id,
                account_id=line.account_id,
                direction=line.direction,
                amount_minor=line.amount_minor,
                currency=currency,
            )
        )
    await session.flush()

    logger.info(
        "journal posted",
        journal_id=str(journal.id),
        kind=kind,
        amount_minor=debit_total,
        currency=currency,
        transfer_id=str(transfer_id) if transfer_id else None,
    )
    return journal


def _signed_amount(natural: Direction) -> ColumnElement[int]:
    """SUM for a balance in an account's natural direction, in one pass over its lines."""
    positive = "debit" if natural == "debit" else "credit"
    return func.coalesce(
        func.sum(
            case(
                (LedgerEntry.direction == positive, LedgerEntry.amount_minor),
                else_=-LedgerEntry.amount_minor,
            )
        ),
        0,
    )


async def balance(session: AsyncSession, *, account_id: uuid.UUID) -> int:
    """What this account holds, in minor units, in its natural direction. Derived every time."""
    account = await session.get(Account, account_id)
    if account is None:
        raise LedgerError(f"no account {account_id}")

    natural = NATURAL_DIRECTION[account.kind]
    result = await session.execute(
        select(_signed_amount(natural)).where(LedgerEntry.account_id == account_id)
    )
    return int(result.scalar_one())


async def trial_balance(session: AsyncSession) -> int:
    """Debits minus credits across the whole ledger. Always zero.

    Non-zero means a trigger has gone missing, which Alembic cannot detect.
    """
    result = await session.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.direction == "debit", LedgerEntry.amount_minor),
                        else_=-LedgerEntry.amount_minor,
                    )
                ),
                0,
            )
        )
    )
    return int(result.scalar_one())


async def system_account(session: AsyncSession, *, kind: str, currency: str) -> Account:
    """The one funding or settlement account for a currency, per a partial unique index."""
    if kind == "recipient_payable":
        raise LedgerError("recipient_payable accounts belong to a recipient; use payable_account")

    result = await session.execute(
        select(Account).where(Account.kind == kind, Account.currency == currency)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise LedgerError(
            f"no {kind} account for {currency}; the chart of accounts has not been seeded"
        )
    return account


async def payable_account(
    session: AsyncSession, *, recipient_id: uuid.UUID, currency: str
) -> Account:
    """This recipient's payable account, created on first use.

    `ON CONFLICT DO NOTHING` rather than read-then-write, so two concurrent
    transfers to one recipient cannot both insert.
    """
    values = {
        "name": f"Payable {recipient_id}",
        "kind": "recipient_payable",
        "currency": currency,
        "recipient_id": recipient_id,
    }
    await session.execute(
        pg_insert(Account)
        .values(**values)
        # The unique index is partial; Postgres only matches it if the
        # predicate is restated.
        .on_conflict_do_nothing(
            index_elements=["recipient_id", "currency"],
            index_where=Account.recipient_id.isnot(None),
        )
    )

    # Not RETURNING: DO NOTHING returns no row on conflict.
    result = await session.execute(
        select(Account).where(
            Account.recipient_id == recipient_id,
            Account.currency == currency,
        )
    )
    return result.scalar_one()


async def lock_account(session: AsyncSession, *, account_id: uuid.UUID) -> None:
    """Take the account's row lock as a mutex, before reading its balance.

    Prevents write skew between concurrent transfers. See AGENTS.md >
    Concurrency: the failure no constraint catches.
    """
    await session.execute(select(Account.id).where(Account.id == account_id).with_for_update())


async def journals_for_transfer(
    session: AsyncSession, *, transfer_id: uuid.UUID
) -> list[JournalEntry]:
    """Every journal for one transfer, oldest first, with lines and accounts loaded eagerly."""
    result = await session.execute(
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines).selectinload(LedgerEntry.account))
        .where(JournalEntry.transfer_id == transfer_id)
        .order_by(JournalEntry.created_at)
    )
    return list(result.scalars())


async def system_accounts(session: AsyncSession) -> list[Account]:
    """The fund and the float: every account that is not a recipient's payable."""
    result = await session.execute(
        select(Account).where(Account.recipient_id.is_(None)).order_by(Account.kind)
    )
    return list(result.scalars())
