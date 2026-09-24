"""Posting through the service, and reading balances back.

The invariants themselves are tested against raw SQL in
`test_ledger_invariants.py` — what is tested here is the layer above: that
`post` refuses a bad journal at the call site with a useful message, that a
balance derived from entries matches what was posted, and that the natural
direction of each account kind is the right way round.

That last one is the test worth having. Getting a direction backwards is the
one mistake that still balances, so the database cannot catch it and no
invariant will ever fire. Only an assertion about what the numbers *mean*
catches it.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Recipient
from app.services import ledger_service
from app.services.ledger_service import Posting

KES = "KES"


async def _accounts(session: AsyncSession) -> tuple[Account, Account, Account]:
    recipient = Recipient(full_name="Asha Mwangi", msisdn="+254700000001", country="KE")
    session.add(recipient)
    await session.flush()

    funding = Account(name="Program fund", kind="program_funding", currency=KES)
    settlement = Account(name="Provider float", kind="provider_settlement", currency=KES)
    payable = Account(
        name="Asha Mwangi", kind="recipient_payable", currency=KES, recipient_id=recipient.id
    )
    session.add_all([funding, settlement, payable])
    await session.flush()
    return funding, settlement, payable


async def _fund(session: AsyncSession, funding: Account, settlement: Account, amount: int) -> None:
    """Money arrives: the provider float goes up and the fund is credited."""
    await ledger_service.post(
        session,
        kind="funding_deposit",
        currency=KES,
        postings=[
            Posting(account_id=settlement.id, direction="debit", amount_minor=amount),
            Posting(account_id=funding.id, direction="credit", amount_minor=amount),
        ],
    )


# ------------------------------------------------------------------ posting


async def test_a_balanced_journal_posts_and_moves_both_balances(session):
    funding, settlement, _ = await _accounts(session)
    await _fund(session, funding, settlement, 1_000_000_00)
    await session.commit()

    # Both read positive: each is in its own natural direction, so a fund with
    # money in it and a float with cash in it are both a positive number.
    assert await ledger_service.balance(session, account_id=funding.id) == 1_000_000_00
    assert await ledger_service.balance(session, account_id=settlement.id) == 1_000_000_00


async def test_authorizing_a_transfer_moves_the_fund_into_a_payable(session):
    """The direction test: authorizing must *reduce* the money left to give away.

    Written as an assertion about meaning rather than about rows, because a
    reversed pair balances perfectly and no database constraint will object.
    """
    funding, settlement, payable = await _accounts(session)
    await _fund(session, funding, settlement, 10_000_00)

    await ledger_service.post(
        session,
        kind="transfer_authorized",
        currency=KES,
        postings=[
            Posting(account_id=funding.id, direction="debit", amount_minor=2_500_00),
            Posting(account_id=payable.id, direction="credit", amount_minor=2_500_00),
        ],
    )
    await session.commit()

    assert await ledger_service.balance(session, account_id=funding.id) == 7_500_00
    assert await ledger_service.balance(session, account_id=payable.id) == 2_500_00
    # Nothing has actually left the provider yet.
    assert await ledger_service.balance(session, account_id=settlement.id) == 10_000_00


async def test_settling_a_transfer_clears_the_payable_and_spends_the_float(session):
    funding, settlement, payable = await _accounts(session)
    await _fund(session, funding, settlement, 10_000_00)
    await ledger_service.post(
        session,
        kind="transfer_authorized",
        currency=KES,
        postings=[
            Posting(account_id=funding.id, direction="debit", amount_minor=2_500_00),
            Posting(account_id=payable.id, direction="credit", amount_minor=2_500_00),
        ],
    )
    await ledger_service.post(
        session,
        kind="transfer_settled",
        currency=KES,
        postings=[
            Posting(account_id=payable.id, direction="debit", amount_minor=2_500_00),
            Posting(account_id=settlement.id, direction="credit", amount_minor=2_500_00),
        ],
    )
    await session.commit()

    # We owe nothing, and the money has actually left the provider float.
    assert await ledger_service.balance(session, account_id=payable.id) == 0
    assert await ledger_service.balance(session, account_id=settlement.id) == 7_500_00
    assert await ledger_service.balance(session, account_id=funding.id) == 7_500_00


async def test_the_trial_balance_is_zero_after_every_posting(session):
    """The one number that says whether the books are sound."""
    funding, settlement, payable = await _accounts(session)
    assert await ledger_service.trial_balance(session) == 0

    await _fund(session, funding, settlement, 10_000_00)
    await session.commit()
    assert await ledger_service.trial_balance(session) == 0

    await ledger_service.post(
        session,
        kind="transfer_authorized",
        currency=KES,
        postings=[
            Posting(account_id=funding.id, direction="debit", amount_minor=999_00),
            Posting(account_id=payable.id, direction="credit", amount_minor=999_00),
        ],
    )
    await session.commit()
    assert await ledger_service.trial_balance(session) == 0


# ----------------------------------------------------------------- refusals


async def test_an_unbalanced_journal_is_refused_at_the_call_site(session):
    """Before it reaches the database, and with the discrepancy in the message."""
    funding, _, payable = await _accounts(session)

    with pytest.raises(ledger_service.UnbalancedError, match="difference 100"):
        await ledger_service.post(
            session,
            kind="transfer_authorized",
            currency=KES,
            postings=[
                Posting(account_id=funding.id, direction="debit", amount_minor=5_00),
                Posting(account_id=payable.id, direction="credit", amount_minor=4_00),
            ],
        )


async def test_a_one_sided_journal_is_refused(session):
    funding, _, _ = await _accounts(session)

    with pytest.raises(ledger_service.UnbalancedError, match="at least two lines"):
        await ledger_service.post(
            session,
            kind="funding_deposit",
            currency=KES,
            postings=[Posting(account_id=funding.id, direction="debit", amount_minor=5_00)],
        )


async def test_a_non_positive_amount_is_refused(session):
    """`direction` carries the sign, so a negative amount is a double negative."""
    funding, _, payable = await _accounts(session)

    with pytest.raises(ledger_service.UnbalancedError, match="amounts are always positive"):
        await ledger_service.post(
            session,
            kind="transfer_authorized",
            currency=KES,
            postings=[
                Posting(account_id=funding.id, direction="debit", amount_minor=-5_00),
                Posting(account_id=payable.id, direction="credit", amount_minor=-5_00),
            ],
        )


async def test_post_does_not_commit(session):
    """Callers compose posting with other writes, so the transaction is theirs.

    If this ever starts passing by accident — because `post` began committing —
    the outbox pattern built on top of it silently stops being atomic: the task
    would be enqueued in a transaction that the ledger write had already ended.
    """
    funding, settlement, _ = await _accounts(session)
    await _fund(session, funding, settlement, 4_200_00)
    await session.rollback()

    remaining = await session.execute(text("select count(*) from ledger_entries"))
    assert remaining.scalar_one() == 0


async def test_a_balance_for_an_unknown_account_is_an_error_not_a_zero(session):
    """Zero is a real balance. Returning it for a typo hides the typo."""
    with pytest.raises(ledger_service.LedgerError, match="no account"):
        await ledger_service.balance(session, account_id=uuid.uuid4())
