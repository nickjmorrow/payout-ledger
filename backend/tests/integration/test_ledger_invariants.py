"""The two rules that make this a ledger, checked against a real Postgres.

Every test here writes **raw SQL** rather than going through a service, and
that is the point. What is being tested is that the *database* refuses bad
books — so a test that could only fail by going through our own code would
prove nothing about a psql session, a data fix, or the next service someone
writes in another language.

The rules:

  1. every journal entry balances, in one currency, across at least two lines;
  2. nothing ever changes a line once it is written.

See the `ledger schema` migration for the trigger bodies and why the first one
has to be deferred.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

KES = "KES"


async def _recipient(session: AsyncSession, msisdn: str = "+254700000001") -> uuid.UUID:
    result = await session.execute(
        text(
            "insert into recipients (full_name, msisdn, country)"
            " values ('Asha Mwangi', :msisdn, 'KE') returning id"
        ),
        {"msisdn": msisdn},
    )
    return result.scalar_one()


async def _account(
    session: AsyncSession,
    kind: str,
    *,
    currency: str = KES,
    recipient_id: uuid.UUID | None = None,
) -> uuid.UUID:
    result = await session.execute(
        text(
            "insert into accounts (name, kind, currency, recipient_id)"
            " values (:name, :kind, :currency, :recipient_id) returning id"
        ),
        {
            "name": f"{kind} ({currency})",
            "kind": kind,
            "currency": currency,
            "recipient_id": recipient_id,
        },
    )
    return result.scalar_one()


async def _journal(session: AsyncSession, kind: str = "funding_deposit") -> uuid.UUID:
    result = await session.execute(
        text("insert into journal_entries (kind) values (:kind) returning id"),
        {"kind": kind},
    )
    return result.scalar_one()


async def _line(
    session: AsyncSession,
    journal_id: uuid.UUID,
    account_id: uuid.UUID,
    direction: str,
    amount_minor: int,
    *,
    currency: str = KES,
) -> uuid.UUID:
    result = await session.execute(
        text(
            "insert into ledger_entries"
            " (journal_entry_id, account_id, direction, amount_minor, currency)"
            " values (:journal, :account, :direction, :amount, :currency) returning id"
        ),
        {
            "journal": journal_id,
            "account": account_id,
            "direction": direction,
            "amount": amount_minor,
            "currency": currency,
        },
    )
    return result.scalar_one()


async def _two_accounts(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    recipient = await _recipient(session)
    funding = await _account(session, "program_funding")
    payable = await _account(session, "recipient_payable", recipient_id=recipient)
    return funding, payable


# ------------------------------------------------------- the balance invariant


async def test_a_balanced_journal_commits(session):
    """The happy path, and the baseline every refusal below is measured against."""
    funding, payable = await _two_accounts(session)
    journal = await _journal(session, "transfer_authorized")

    await _line(session, journal, funding, "debit", 5_000_00)
    await _line(session, journal, payable, "credit", 5_000_00)
    await session.commit()

    total = await session.execute(
        text("select count(*) from ledger_entries where journal_entry_id = :j"), {"j": journal}
    )
    assert total.scalar_one() == 2


async def test_the_check_is_deferred_to_commit_not_run_per_row(session):
    """The first line of a journal is always unbalanced, and must be allowed.

    This is the test that would fail if someone "simplified" the constraint
    trigger to an immediate one. It would not look like a correctness bug — it
    would look like every posting in the system suddenly being rejected, which
    is at least loud. The subtler failure is the reverse, so the two tests are
    a pair: this one proves the check is late enough, and the one below proves
    it still happens.
    """
    funding, payable = await _two_accounts(session)
    journal = await _journal(session, "transfer_authorized")

    # Unbalanced right now, and deliberately not an error yet.
    await _line(session, journal, funding, "debit", 1_000_00)
    await session.flush()

    await _line(session, journal, payable, "credit", 1_000_00)
    await session.commit()


async def test_an_unbalanced_journal_is_refused_at_commit(session):
    """Debits and credits that do not agree never reach the table."""
    funding, payable = await _two_accounts(session)
    journal = await _journal(session, "transfer_authorized")

    await _line(session, journal, funding, "debit", 5_000_00)
    await _line(session, journal, payable, "credit", 4_999_00)

    with pytest.raises(Exception, match="does not balance"):
        await session.commit()
    await session.rollback()


async def test_a_one_sided_journal_is_refused(session):
    """A single line balances against nothing; it is a number, not a posting."""
    funding, _ = await _two_accounts(session)
    journal = await _journal(session)

    await _line(session, journal, funding, "debit", 1_00)

    with pytest.raises(Exception, match="at least two accounts"):
        await session.commit()
    await session.rollback()


async def test_a_journal_may_not_mix_currencies(session):
    """100 KES against 100 USD sums to zero in minor units and is not balanced.

    The most expensive bug this table can have, because it looks correct to
    every check that only adds up integers. Cross-currency movement is two
    journals and an explicit FX leg.
    """
    recipient = await _recipient(session)
    kes_funding = await _account(session, "program_funding", currency="KES")
    usd_payable = await _account(
        session, "recipient_payable", currency="USD", recipient_id=recipient
    )
    journal = await _journal(session)

    await _line(session, journal, kes_funding, "debit", 100_00, currency="KES")
    await _line(session, journal, usd_payable, "credit", 100_00, currency="USD")

    with pytest.raises(Exception, match="mixes 2 currencies"):
        await session.commit()
    await session.rollback()


async def test_a_zero_or_negative_amount_is_refused(session):
    """Both would still balance, and neither is a posting.

    A CHECK rather than the trigger: this is true of a single row on its own,
    so there is nothing to defer and the error should point at the line.
    """
    funding, _ = await _two_accounts(session)
    journal = await _journal(session)

    for amount in (0, -1_00):
        with pytest.raises(Exception, match="ledger_entries_amount_check"):
            await _line(session, journal, funding, "debit", amount)
        await session.rollback()


# ------------------------------------------------------------- append-only


async def test_a_posted_line_cannot_be_updated(session):
    """Corrections are reversing entries, which leave both sides in the history."""
    funding, payable = await _two_accounts(session)
    journal = await _journal(session, "transfer_authorized")
    line = await _line(session, journal, funding, "debit", 2_000_00)
    await _line(session, journal, payable, "credit", 2_000_00)
    await session.commit()

    with pytest.raises(Exception, match="append-only"):
        await session.execute(
            text("update ledger_entries set amount_minor = 1 where id = :id"), {"id": line}
        )
    await session.rollback()


async def test_a_posted_line_cannot_be_deleted(session):
    """Deleting one leg would leave the journal permanently unbalanced.

    And it would do so *silently*: the balance trigger fires on INSERT, so
    nothing re-checks a journal whose lines are removed afterwards. This
    trigger is what closes that hole.
    """
    funding, payable = await _two_accounts(session)
    journal = await _journal(session, "transfer_authorized")
    line = await _line(session, journal, funding, "debit", 2_000_00)
    await _line(session, journal, payable, "credit", 2_000_00)
    await session.commit()

    with pytest.raises(Exception, match="append-only"):
        await session.execute(text("delete from ledger_entries where id = :id"), {"id": line})
    await session.rollback()


# ------------------------------------------------------------ account shape


async def test_only_a_recipient_payable_account_names_a_recipient(session):
    """An unattributed payable is money reconciliation cannot assign to anybody."""
    recipient = await _recipient(session)

    with pytest.raises(Exception, match="accounts_recipient_kind_check"):
        await _account(session, "recipient_payable")
    await session.rollback()

    with pytest.raises(Exception, match="accounts_recipient_kind_check"):
        await _account(session, "program_funding", recipient_id=recipient)
    await session.rollback()


async def test_a_recipient_has_at_most_one_payable_account_per_currency(session):
    """Two payable accounts for one recipient is a balance split in half."""
    recipient = await _recipient(session)
    await _account(session, "recipient_payable", recipient_id=recipient)
    await session.commit()

    with pytest.raises(Exception, match="accounts_recipient_currency_idx"):
        await _account(session, "recipient_payable", recipient_id=recipient)
    await session.rollback()

    # A different currency is a different account, and is allowed.
    await _account(session, "recipient_payable", currency="USD", recipient_id=recipient)
    await session.commit()
