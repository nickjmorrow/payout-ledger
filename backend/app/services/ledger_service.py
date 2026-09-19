"""Posting to the books, and reading balances back out.

**Every write to `ledger_entries` goes through `post`.** Not because the
database would let a bad journal through otherwise — it would not, that is what
the triggers are for — but because a caller assembling lines by hand has to get
the directions right, and getting them wrong is the one mistake that still
balances. `post` takes the two halves together and refuses anything else.

The Python checks here duplicate the database triggers on purpose. They are not
the enforcement; they are the error message. A caller that passes an unbalanced
journal gets an `UnbalancedError` naming the discrepancy at the call site, rather than
an opaque `check_violation` raised at COMMIT from somewhere else entirely. If
the two ever disagree, the database is right.

**Nothing here commits.** Posting to the books is almost never the only thing a
request does — it happens alongside creating a transfer, recording an
idempotency key, and enqueueing the work that will actually move the money —
and all of those have to land or not land together. Committing here would end
the caller's transaction early and make that impossible. Callers commit.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

# Which direction increases each kind of account.
#
# This is the one piece of accounting that cannot be derived and must simply be
# known: assets go up on a debit, liabilities and equity go up on a credit. It
# is written down here so that `balance` can return a number that reads the way
# a person expects — a funding account with money left in it is positive — and
# so that nothing else in the codebase has to remember which way round it goes.
NATURAL_DIRECTION: dict[str, Direction] = {
    # Equity: the pool of donated money. Credited when funded, debited when
    # given away.
    "program_funding": "credit",
    # Liability: what we owe a recipient until the provider confirms payment.
    "recipient_payable": "credit",
    # Asset: our cash balance held at the provider.
    "provider_settlement": "debit",
}


class LedgerError(Exception):
    """Base for every refusal in this module."""


class UnbalancedError(LedgerError):
    """Debits and credits do not agree, so this is not a posting."""


# A posting moves money between accounts, so two lines is the floor. Named
# rather than inline so the refusal below reads as a rule and not a magic 2.
MINIMUM_LINES = 2


@dataclass(frozen=True)
class Posting:
    """One line, before it is a row.

    `amount_minor` is always positive; `direction` carries the sign. A signed
    amount plus a direction gives two ways to say "credit 500", one of which is
    a double negative waiting to be misread.
    """

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

    `currency` is a parameter of the journal rather than of each line, which
    makes the mixed-currency journal the trigger refuses unrepresentable here
    in the first place. Cross-currency movement is two journals and an explicit
    FX leg.
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
    # Flushed rather than committed, so the lines below have a journal id to
    # point at while staying inside the caller's transaction.
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
    """SUM expression for a balance in an account's natural direction.

    Built as a CASE inside one aggregate rather than two separate sums
    subtracted, so the balance is one index scan over the account's lines
    instead of two.
    """
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
    """What this account holds, in minor units, in its natural direction.

    Derived from the entries every time rather than read from a column. See the
    note on `Account` for why there is no stored balance and what to do when
    this stops being fast enough.
    """
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

    The one number that says whether the books are sound. If this is ever
    non-zero, something has written to `ledger_entries` without going through a
    balanced journal — which the triggers should make impossible, so a non-zero
    answer here means a trigger is missing rather than that a posting was
    wrong. That is exactly the failure Alembic cannot warn about, which is why
    this is cheap to expose and worth watching.
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
