"""The worker actually moving money: sending, settling, retrying, giving up.

The tests that matter here are the ones about a worker dying at the worst
possible moment, because that is the case the whole design is arranged around.
A payment accepted by the provider but not yet recorded by us is the single
most expensive state this system can be in, and the only thing standing between
it and a double payment is asking before acting.
"""

import uuid

import pytest
from sqlalchemy import text

from app.config import settings
from app.models import Task, Transfer
from app.provider.base import ProviderError, ProviderPaymentView
from app.provider.mock import MockProvider
from app.services import ledger_service, task_service, transfer_service
from app.worker import disburse
from app.worker.handlers import execute
from tests.support.program import enroll, open_program

KES = "KES"
FUND = 100_000_00


@pytest.fixture(autouse=True)
def settle_immediately(monkeypatch):
    monkeypatch.setattr(settings, "provider_settle_after_seconds", 0)


@pytest.fixture
async def funded(session):
    funding, settlement = await open_program(session, fund_minor=FUND)
    return funding, settlement, await enroll(session)


async def _initiate(session, recipient, amount: int = 2_500_00) -> Transfer:
    transfer = await transfer_service.initiate(
        session, recipient_id=recipient.id, amount_minor=amount, currency=KES
    )
    await session.commit()
    return transfer


async def _claim(session) -> Task:
    """Claim the next task, pulling anything scheduled into the present first.

    The settle check is deliberately enqueued a few seconds out, and a retry
    backs off further still, so a test that simply claimed would find nothing
    and have to sleep. Moving `run_at` is honest about which part of the
    behavior is being skipped: the waiting, not the scheduling.
    """
    await session.execute(text("update tasks set run_at = now() where status = 'pending'"))
    await session.commit()

    task = await task_service.claim_next(session, worker_id="w1")
    assert task is not None
    return task


# ------------------------------------------------------------- the happy path


async def test_a_disbursement_is_sent_then_settled(session, funded):
    _, settlement, recipient = funded
    transfer = await _initiate(session, recipient)

    await execute(await _claim(session))
    await session.refresh(transfer)
    assert transfer.status == "processing"
    assert transfer.provider_reference is not None

    # Sending scheduled a check rather than asking straight away.
    await execute(await _claim(session))
    await session.refresh(transfer)
    assert transfer.status == "succeeded"

    # The money has left the float, and the books still balance.
    assert await ledger_service.balance(session, account_id=settlement.id) == FUND - 2_500_00
    assert await ledger_service.trial_balance(session) == 0


async def test_settling_clears_what_the_recipient_was_owed(session, funded):
    _, _, recipient = funded
    transfer = await _initiate(session, recipient)
    payable = await ledger_service.payable_account(session, recipient_id=recipient.id, currency=KES)
    assert await ledger_service.balance(session, account_id=payable.id) == 2_500_00

    await execute(await _claim(session))
    await execute(await _claim(session))
    await session.refresh(transfer)

    assert transfer.status == "succeeded"
    assert await ledger_service.balance(session, account_id=payable.id) == 0


# ------------------------------------------------------- the dangerous cases


async def test_a_redelivered_send_does_not_pay_twice(session, funded):
    """A worker killed after the provider accepted, before the row was written.

    The sweeper requeues the task and it runs again from the top. Without the
    status check in `disburse`, that is a second payment — and relying on the
    provider's dedupe to catch our own mistake is not a design.
    """
    _, _, recipient = funded
    transfer = await _initiate(session, recipient)

    task = await _claim(session)
    await execute(task)
    await session.refresh(transfer)
    first_reference = transfer.provider_reference

    # Simulate the sweeper handing the same task back.
    await session.execute(
        text("update tasks set status='pending', claimed_at=null where id=:id"),
        {"id": task.id},
    )
    await session.commit()
    await execute(await _claim(session))

    await session.refresh(transfer)
    assert transfer.provider_reference == first_reference

    payments = await MockProvider().list_payments(since=transfer.created_at.replace(microsecond=0))
    assert len(payments) == 1, "the recipient was paid twice"


async def test_the_provider_idempotency_key_is_the_transfer_id(session, funded):
    """Stable across every retry, which is what makes at-least-once safe."""
    _, _, recipient = funded
    transfer = await _initiate(session, recipient)
    await execute(await _claim(session))

    payments = await MockProvider().list_payments(since=transfer.created_at.replace(microsecond=0))
    assert payments[0].idempotency_key == str(transfer.id)


# ------------------------------------------------------------------ failures


async def test_an_unreachable_provider_is_retried_not_abandoned(session, funded, monkeypatch):
    _, _, recipient = funded
    transfer = await _initiate(session, recipient)
    monkeypatch.setattr(settings, "provider_unreachable", True)

    task = await _claim(session)
    await execute(task)

    await session.refresh(task)
    assert task.status == "pending"
    # The transfer is untouched: it has not failed, it has not been sent.
    await session.refresh(transfer)
    assert transfer.status == "pending"


async def test_a_rejecting_provider_reverses_the_transfer_immediately(session, funded, monkeypatch):
    """Not retryable, so there is nothing to wait for. Give the money back now."""
    funding, _, recipient = funded
    transfer = await _initiate(session, recipient)
    monkeypatch.setattr(settings, "provider_reject_all", True)

    await execute(await _claim(session))

    await session.refresh(transfer)
    assert transfer.status == "failed"
    assert await ledger_service.balance(session, account_id=funding.id) == FUND
    assert await ledger_service.trial_balance(session) == 0


async def test_exhausting_the_retries_reverses_the_transfer(session, funded, monkeypatch):
    """The dead-letter queue must not leak money.

    A task that runs out of attempts is parked as `failed` for a human. If
    nothing else happened the transfer would sit at `pending` forever with the
    fund still debited — money promised to somebody who will never receive it.
    """
    funding, _, recipient = funded
    transfer = await _initiate(session, recipient)
    monkeypatch.setattr(settings, "provider_unreachable", True)

    # Three attempts, which is `max_attempts`. `_claim` pulls each backed-off
    # retry into the present, so what is exercised is the budget running out
    # rather than the delay between tries.
    for _ in range(3):
        await execute(await _claim(session))

    await session.refresh(transfer)
    assert transfer.status == "failed"
    assert "unreachable" in (transfer.failure_reason or "")

    # The money is back, the books balance, and the task is dead-lettered.
    assert await ledger_service.balance(session, account_id=funding.id) == FUND
    assert await ledger_service.trial_balance(session) == 0
    dead = await task_service.dead_lettered(session)
    assert len(dead) == 1
    assert dead[0].kind == transfer_service.DISBURSE


async def test_a_payment_still_pending_reschedules_rather_than_failing(
    session, funded, monkeypatch
):
    """A slow payment is an answer, not an error. Retries are for errors."""
    _, _, recipient = funded
    transfer = await _initiate(session, recipient)
    await execute(await _claim(session))

    # Now make the provider hold it pending.
    monkeypatch.setattr(settings, "provider_settle_after_seconds", 3600)
    task = await _claim(session)
    await execute(task)

    await session.refresh(transfer)
    assert transfer.status == "processing"
    # A fresh check is queued; the retry budget was not spent.
    await session.refresh(task)
    assert task.status == "succeeded"
    pending = await task_service.pending_count(session, kind=disburse.SETTLE)
    assert pending == 1


async def test_a_provider_failure_at_settlement_reverses_the_transfer(session, funded, monkeypatch):
    """Accepted, then lost. The money looked like it was on its way."""
    funding, _, recipient = funded
    transfer = await _initiate(session, recipient)
    await execute(await _claim(session))

    monkeypatch.setattr(settings, "provider_fail_settlement", True)
    await execute(await _claim(session))

    await session.refresh(transfer)
    assert transfer.status == "failed"
    assert await ledger_service.balance(session, account_id=funding.id) == FUND
    assert await ledger_service.trial_balance(session) == 0


async def test_a_task_naming_no_transfer_fails_without_retrying(session):
    """A bad payload does not get better by trying again."""
    task = await task_service.enqueue(
        session, kind=transfer_service.DISBURSE, payload={"transfer_id": str(uuid.uuid4())}
    )
    await session.commit()

    claimed = await _claim(session)
    await execute(claimed)
    await session.refresh(task)
    assert task.status == "failed"
    assert task.attempts == 1, "a missing transfer should not consume the retry budget"


async def test_a_fake_provider_can_be_substituted(session, funded, monkeypatch):
    """The seam holds: nothing here needed the mock to be patched internally."""
    _, _, recipient = funded
    transfer = await _initiate(session, recipient)

    class AlwaysFails:
        async def send_payment(self, **_: object) -> ProviderPaymentView:
            raise ProviderError("nope", retryable=False)

    always_fails = AlwaysFails()
    monkeypatch.setattr(disburse, "_provider", lambda: always_fails)

    await execute(await _claim(session))
    await session.refresh(transfer)
    assert transfer.status == "failed"
    assert transfer.failure_reason == "nope"
