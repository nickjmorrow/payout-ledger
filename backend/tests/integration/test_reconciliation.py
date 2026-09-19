"""Reconciliation: finding the failures that leave no error anywhere.

Every scenario here is built by putting the two sides *deliberately* out of
step — which is the only way to test this, because none of these states can be
reached by the application working correctly. That is also the point: they are
reachable in production by a crash at the wrong moment, and nothing else in the
system would notice.

The split that gets the most attention is which findings heal and which only
report. Healing the wrong one silently moves money on the strength of a single
disagreement.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.config import settings
from app.models import Account, Recipient, ReconciliationFinding, Transfer
from app.provider.mock import MockProvider
from app.services import (
    ledger_service,
    reconciliation_service,
    task_service,
    transfer_service,
)
from app.services.ledger_service import Posting
from app.worker import reconcile
from app.worker.handlers import execute

KES = "KES"
FUND = 100_000_00


@pytest.fixture(autouse=True)
def settle_immediately(monkeypatch):
    monkeypatch.setattr(settings, "provider_settle_after_seconds", 0)


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
async def funded(session):
    funding = Account(name="Fund", kind="program_funding", currency=KES)
    settlement = Account(name="Float", kind="provider_settlement", currency=KES)
    recipient = Recipient(full_name="Asha Mwangi", msisdn="+254700000001", country="KE")
    session.add_all([funding, settlement, recipient])
    await session.flush()
    await ledger_service.post(
        session,
        kind="funding_deposit",
        currency=KES,
        postings=[
            Posting(account_id=settlement.id, direction="debit", amount_minor=FUND),
            Posting(account_id=funding.id, direction="credit", amount_minor=FUND),
        ],
    )
    await session.commit()
    return funding, settlement, recipient


async def _transfer(session, recipient, *, amount=2_500_00) -> Transfer:
    transfer = await transfer_service.initiate(
        session, recipient_id=recipient.id, amount_minor=amount, currency=KES
    )
    await session.commit()
    return transfer


async def _sent(session, provider, recipient, *, amount=2_500_00) -> Transfer:
    """A transfer the provider has accepted and we have recorded as processing."""
    transfer = await _transfer(session, recipient, amount=amount)
    payment = await provider.send_payment(
        idempotency_key=str(transfer.id),
        msisdn=recipient.msisdn,
        amount_minor=amount,
        currency=KES,
    )
    await transfer_service.mark_processing(
        session, transfer=transfer, provider_reference=payment.reference
    )
    await session.commit()
    return transfer


def _kinds(report) -> list[str]:
    return sorted(f.kind for f in report.findings)


# -------------------------------------------------------------- agreement


async def test_a_ledger_that_agrees_produces_no_findings(session, provider, funded):
    _, _, recipient = funded
    await _transfer(session, recipient)

    report = await reconciliation_service.run(session, provider=provider)
    assert report.findings == []
    assert report.checked == 1


async def test_a_transfer_not_yet_sent_is_not_drift(session, provider, funded):
    """Pending means we have not handed it over. There is nothing to disagree with."""
    _, _, recipient = funded
    await _transfer(session, recipient)

    report = await reconciliation_service.run(session, provider=provider)
    assert _kinds(report) == []


# ----------------------------------------------------------------- healing


async def test_a_settlement_we_missed_is_healed(session, provider, funded):
    """The benign case: they settled, we stopped polling. They are authoritative.

    This is the one that makes reconciliation worth running rather than just
    worth alerting on — the common drift repairs itself.
    """
    _funding, settlement, recipient = funded
    transfer = await _sent(session, provider, recipient)

    # The provider settles; nothing tells us.
    await provider.advance_pending()

    report = await reconciliation_service.run(session, provider=provider)
    await session.commit()

    assert _kinds(report) == ["status_behind"]
    assert report.healed == 1
    assert report.unresolved == 0

    await session.refresh(transfer)
    assert transfer.status == "succeeded"
    # And the books actually moved, not just the status column.
    assert await ledger_service.balance(session, account_id=settlement.id) == FUND - 2_500_00
    assert await ledger_service.trial_balance(session) == 0


async def test_a_failure_we_missed_is_healed_and_returns_the_money(
    session, provider, funded, monkeypatch
):
    funding, _, recipient = funded
    transfer = await _sent(session, provider, recipient)

    monkeypatch.setattr(settings, "provider_fail_settlement", True)
    await provider.advance_pending()

    report = await reconciliation_service.run(session, provider=provider)
    await session.commit()

    assert _kinds(report) == ["status_behind"]
    await session.refresh(transfer)
    assert transfer.status == "failed"
    assert await ledger_service.balance(session, account_id=funding.id) == FUND
    assert await ledger_service.trial_balance(session) == 0


# --------------------------------------------------------------- reporting


async def test_a_payment_the_provider_has_no_record_of_is_reported_not_healed(
    session, provider, funded
):
    """Either our send never landed or they lost it. Those need opposite fixes."""
    _, _, recipient = funded
    transfer = await _transfer(session, recipient)
    await transfer_service.mark_processing(
        session, transfer=transfer, provider_reference="MMNEVEREXISTED"
    )
    await session.commit()

    report = await reconciliation_service.run(session, provider=provider)
    await session.commit()

    assert _kinds(report) == ["missing_at_provider"]
    assert report.healed == 0
    assert report.unresolved == 1
    # Untouched: a machine cannot tell which way this one goes.
    await session.refresh(transfer)
    assert transfer.status == "processing"


async def test_a_payment_we_cannot_match_is_reported(session, provider, funded):
    """The most serious finding: money left the float without the books knowing."""
    _, _, recipient = funded
    await provider.send_payment(
        idempotency_key="not-ours",
        msisdn=recipient.msisdn,
        amount_minor=5_000_00,
        currency=KES,
    )

    report = await reconciliation_service.run(session, provider=provider)
    await session.commit()

    assert _kinds(report) == ["unknown_to_us"]
    assert report.unresolved == 1


async def test_a_contradiction_is_never_healed_automatically(session, provider, funded):
    """We paid them, they say it failed. Our settlement journal already moved money.

    Unwinding that on one disagreement is how a provider glitch becomes a
    reversal storm, so this stops at a finding and waits for a person.
    """
    _, _, recipient = funded
    transfer = await _sent(session, provider, recipient)
    await transfer_service.mark_succeeded(session, transfer=transfer)
    await session.commit()

    # Now the provider says it failed.
    await session.execute(
        text("update provider_payments set status='failed', failure_reason='reversed by bank'")
    )
    await session.commit()

    report = await reconciliation_service.run(session, provider=provider)
    await session.commit()

    assert _kinds(report) == ["status_contradicted"]
    assert report.healed == 0
    await session.refresh(transfer)
    assert transfer.status == "succeeded", "a contradiction must not be auto-reversed"


async def test_a_reversal_the_provider_actually_paid_is_reported(session, provider, funded):
    """We gave the money back to the fund; they paid the recipient anyway.

    The programme has now paid out money its books show as still available,
    which is the worst of the reportable findings and deserves its own case.
    """
    _, _, recipient = funded
    transfer = await _sent(session, provider, recipient)
    await transfer_service.mark_failed(session, transfer=transfer, reason="timed out")
    await session.commit()
    await provider.advance_pending()

    report = await reconciliation_service.run(session, provider=provider)
    await session.commit()

    assert _kinds(report) == ["status_contradicted"]
    assert "still available" in report.findings[0].detail


async def test_an_amount_mismatch_is_reported_and_stops_further_comparison(
    session, provider, funded
):
    """With the amounts disagreeing, healing the status would settle the wrong number."""
    _, _, recipient = funded
    transfer = await _sent(session, provider, recipient)
    await provider.advance_pending()

    await session.execute(text("update provider_payments set amount_minor = 9_999_00"))
    await session.commit()

    report = await reconciliation_service.run(session, provider=provider)
    await session.commit()

    assert _kinds(report) == ["amount_mismatch"]
    assert report.healed == 0
    await session.refresh(transfer)
    assert transfer.status == "processing", "must not settle an amount we disagree about"


# --------------------------------------------------------------- the record


async def test_findings_are_persisted_for_somebody_to_read(session, provider, funded):
    _, _, recipient = funded
    transfer = await _transfer(session, recipient)
    await transfer_service.mark_processing(session, transfer=transfer, provider_reference="MMGONE")
    await session.commit()

    await reconciliation_service.run(session, provider=provider)
    await session.commit()

    stored = (await session.execute(select(ReconciliationFinding))).scalars().all()
    assert len(stored) == 1
    assert stored[0].kind == "missing_at_provider"
    assert stored[0].transfer_id == transfer.id
    assert not stored[0].healed


async def test_the_window_bounds_the_work(session, provider, funded):
    """A job whose cost grows with total history is one that stops completing."""
    _, _, recipient = funded
    await _sent(session, provider, recipient)

    report = await reconciliation_service.run(
        session, provider=provider, since=datetime.now(UTC) + timedelta(hours=1)
    )
    assert report.checked == 0
    assert report.findings == []


# ------------------------------------------------------------- the schedule


async def test_the_pass_reschedules_itself(session, funded):
    """The cadence lives in the queue, so a restart does not lose it."""
    await reconcile.ensure_scheduled(session)
    assert await task_service.pending_count(session, kind=reconcile.RECONCILE) == 1

    await session.execute(
        text("update tasks set run_at = now() where kind = :k"), {"k": reconcile.RECONCILE}
    )
    await session.commit()

    await execute(await _claim(session))

    # Ran, settled, and left its successor behind.
    assert await task_service.pending_count(session, kind=reconcile.RECONCILE) == 1


async def test_seeding_is_idempotent(session):
    """Called on every worker pass, so it must not pile up schedules."""
    await reconcile.ensure_scheduled(session)
    await reconcile.ensure_scheduled(session)
    await reconcile.ensure_scheduled(session)
    assert await task_service.pending_count(session, kind=reconcile.RECONCILE) == 1


async def _claim(session):
    task = await task_service.claim_next(session, worker_id="w1")
    assert task is not None
    return task
