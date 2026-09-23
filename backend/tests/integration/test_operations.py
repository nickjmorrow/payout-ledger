"""The operator's view of the queue, and the one thing they can do to it.

Retrying a dead letter is the only write here, and the tests are mostly about
when it is refused: a retry that re-ran a finished payment's task would at best
do nothing and at worst tell an operator a payment had been sent again.
"""

import httpx
import pytest
from sqlalchemy import text

from app.bus import bus
from app.config import settings
from app.main import app
from app.models import Account, Recipient
from app.provider.mock import MockProvider
from app.services import ledger_service, task_service, transfer_service
from app.services.ledger_service import Posting
from app.worker import disburse
from app.worker.handlers import execute

KES = "KES"
FUND = 100_000_00

# Registers `disburse_transfer`. Without it the registry is empty whenever this
# file runs alone, and the retried send fails as an "unknown task kind" — the
# trap `HANDLER_MODULES` in worker/loop.py describes. Named so the import is
# used rather than left for a linter to delete.
HANDLER_MODULES = (disburse,)


@pytest.fixture(autouse=True)
def settle_immediately(monkeypatch):
    monkeypatch.setattr(settings, "provider_settle_after_seconds", 0)


@pytest.fixture
async def client():
    await bus.start()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
    finally:
        await bus.stop()


@pytest.fixture
async def recipient(session):
    fund = Account(name="Fund", kind="program_funding", currency=KES)
    float_ = Account(name="Float", kind="provider_settlement", currency=KES)
    person = Recipient(full_name="Asha Mwangi", msisdn="+254700000001", country="KE")
    session.add_all([fund, float_, person])
    await session.flush()
    await ledger_service.post(
        session,
        kind="funding_deposit",
        currency=KES,
        postings=[
            Posting(account_id=float_.id, direction="debit", amount_minor=FUND),
            Posting(account_id=fund.id, direction="credit", amount_minor=FUND),
        ],
    )
    await session.commit()
    return person


async def _dead(session, task_id, *, attempts=3):
    """Park a task in the dead-letter queue, as exhausting its retries would."""
    await session.execute(
        text(
            "update tasks set status = 'failed', attempts = :n, max_attempts = :n,"
            " error = 'provider unreachable' where id = :id"
        ),
        {"id": task_id, "n": attempts},
    )
    await session.commit()


async def _disburse_task(session, transfer_id):
    result = await session.execute(
        text(
            "select id from tasks where kind = 'disburse_transfer' and payload->>'transfer_id' = :t"
        ),
        {"t": str(transfer_id)},
    )
    return result.scalar_one()


# ------------------------------------------------------------------ the queue


async def test_the_queue_tells_due_work_from_scheduled_work(client, session):
    """A dozen settlement checks a few seconds out is a healthy queue, not a backlog."""
    await task_service.enqueue(session, kind="noop")
    await task_service.enqueue(session, kind="noop", run_at=task_service.seconds_from_now(60))
    await session.commit()

    data = (await client.get("/api/queue")).json()["data"]
    assert (data["due"], data["scheduled"], data["running"], data["dead"]) == (1, 1, 0, 0)
    assert len(data["active"]) == 2


# ------------------------------------------------------------------ retrying


async def test_a_dead_letter_about_no_transfer_is_retried_with_a_fresh_budget(client, session):
    task = await task_service.enqueue(session, kind="reconcile")
    await session.commit()
    await _dead(session, task.id)

    response = await client.post(f"/api/dead-letters/{task.id}/retry")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "pending"
    # Extended, not reset: how it got to the dead-letter queue stays readable.
    assert (data["attempts"], data["maxAttempts"]) == (3, 6)
    assert data["error"] == "provider unreachable"

    overview = (await client.get("/api/overview")).json()["data"]
    assert overview["deadLettered"] == 0


async def test_a_dead_letter_for_a_reversed_payment_is_not_retried(client, session, recipient):
    """The money is already back in the fund. Running the send again would do nothing."""
    transfer = await transfer_service.initiate(
        session, recipient_id=recipient.id, amount_minor=2_500_00, currency=KES
    )
    await session.commit()
    task_id = await _disburse_task(session, transfer.id)
    await transfer_service.mark_failed(session, transfer=transfer, reason="provider unreachable")
    await session.commit()
    await _dead(session, task_id)

    response = await client.post(f"/api/dead-letters/{task_id}/retry")
    assert response.status_code == 409
    assert "was reversed" in response.json()["detail"]
    assert "authorise a new disbursement" in response.json()["detail"]

    still = await session.execute(text("select status from tasks where id = :id"), {"id": task_id})
    assert still.scalar_one() == "failed"


async def test_a_dead_letter_for_an_unfinished_payment_is_retried_and_pays_once(
    client, session, recipient
):
    """The case retry exists for: a send dead-lettered with its transfer still pending.

    This is what the sweeper leaves behind when a worker dies on a task's last
    attempt — the fund debited, and nothing that will ever move it. Retrying
    must send the payment, and send it once.
    """
    transfer = await transfer_service.initiate(
        session, recipient_id=recipient.id, amount_minor=2_500_00, currency=KES
    )
    await session.commit()
    task_id = await _disburse_task(session, transfer.id)
    await _dead(session, task_id)

    assert (await client.post(f"/api/dead-letters/{task_id}/retry")).status_code == 200

    claimed = await task_service.claim_next(session, worker_id="w1")
    assert claimed is not None
    assert claimed.id == task_id
    await execute(claimed)

    await session.refresh(transfer)
    assert transfer.status == "processing"
    payments = await MockProvider().list_payments(since=transfer.created_at.replace(microsecond=0))
    assert len(payments) == 1


async def test_retrying_twice_is_refused_the_second_time(client, session):
    """No idempotency key: the state check is the guard, and a repeat is refused, not doubled."""
    task = await task_service.enqueue(session, kind="reconcile")
    await session.commit()
    await _dead(session, task.id)

    assert (await client.post(f"/api/dead-letters/{task.id}/retry")).status_code == 200
    second = await client.post(f"/api/dead-letters/{task.id}/retry")
    assert second.status_code == 409
    assert "not in the dead-letter queue" in second.json()["detail"]
