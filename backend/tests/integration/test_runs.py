"""Payment runs: many transfers, one decision.

The property that gets the most attention is all-or-nothing. A run that
authorised some of its transfers and refused the rest would hand an operator a
list to reconcile by hand against a fund that had moved underneath them — and
the chance of paying someone twice is the chance they get that list wrong.
"""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text

from app.bus import bus
from app.db import SessionFactory
from app.main import app
from app.models import Account, PaymentRun, Recipient, Transfer
from app.services import ledger_service, run_service, transfer_service
from app.services.ledger_service import Posting
from app.services.run_service import RunItem

KES = "KES"
FUND = 10_000_00
EACH = 2_500_00


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
async def funding(session):
    fund = Account(name="Fund", kind="program_funding", currency=KES)
    float_ = Account(name="Float", kind="provider_settlement", currency=KES)
    session.add_all([fund, float_])
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
    return fund


@pytest.fixture
async def people(session):
    rows = [
        Recipient(full_name=f"Recipient {n}", msisdn=f"+2547000009{n:02d}", country="KE")
        for n in range(3)
    ]
    session.add_all(rows)
    await session.commit()
    return rows


def _body(recipients, amount=EACH, memo="September cycle") -> dict[str, Any]:
    return {
        "items": [{"recipientId": str(r.id), "amountMinor": amount} for r in recipients],
        "currency": KES,
        "memo": memo,
    }


async def _post(client, body, key="run-key-1"):
    return await client.post("/api/runs", json=body, headers={"Idempotency-Key": key})


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_a_run_authorises_every_transfer_and_debits_the_fund_by_the_total(
    client, session, funding, people
):
    response = await _post(client, _body(people))
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["count"] == 3
    assert data["totalMinor"] == 3 * EACH
    assert data["byStatus"] == {"pending": 3}

    # What the numbers mean: the money available to give away fell by exactly
    # what the run promised, and nothing else moved.
    assert await ledger_service.balance(session, account_id=funding.id) == FUND - 3 * EACH
    assert await ledger_service.trial_balance(session) == 0

    queued = await session.execute(
        text("select count(*) from tasks where kind = 'disburse_transfer'")
    )
    assert queued.scalar_one() == 3


async def test_one_unknown_recipient_refuses_the_whole_run(client, session, funding, people):
    """All or nothing: the two real recipients must not be paid either."""
    body = _body(people[:2])
    body["items"].insert(1, {"recipientId": str(uuid.uuid4()), "amountMinor": EACH})

    response = await _post(client, body)
    assert response.status_code == 404

    assert await _count(session, Transfer) == 0
    assert await _count(session, PaymentRun) == 0
    assert await ledger_service.balance(session, account_id=funding.id) == FUND


async def test_a_run_larger_than_the_fund_is_refused_with_the_total(
    client, session, funding, people
):
    """The message is about the run, not about whichever recipient tipped it over."""
    response = await _post(client, _body(people, amount=4_000_00))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "This run totals KES 12,000.00" in detail
    assert "across 3 recipients" in detail
    assert await _count(session, Transfer) == 0


async def test_the_same_recipient_twice_is_refused(client, session, funding, people):
    response = await _post(client, _body([people[0], people[1], people[0]]))
    assert response.status_code == 422
    assert "more than once" in response.json()["detail"]
    assert await _count(session, Transfer) == 0


async def test_a_retried_run_is_one_run(client, session, funding, people):
    """The most expensive request to retry by accident: every recipient, twice."""
    first = await _post(client, _body(people), key="retry-run")
    second = await _post(client, _body(people), key="retry-run")

    assert second.status_code == 201
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    assert await _count(session, Transfer) == 3
    assert await ledger_service.balance(session, account_id=funding.id) == FUND - 3 * EACH


async def test_progress_is_counted_from_the_transfers(client, session, funding, people):
    """There is no status on a run to get out of step with its transfers."""
    run_id = (await _post(client, _body(people))).json()["data"]["id"]

    # A fresh transaction, so `now()` is later than the API's commit.
    await session.rollback()
    transfers = await transfer_service.recent(session, run_id=uuid.UUID(run_id))
    await transfer_service.mark_processing(session, transfer=transfers[0], provider_reference="MM1")
    await transfer_service.mark_succeeded(session, transfer=transfers[0])
    await transfer_service.mark_failed(session, transfer=transfers[1], reason="wallet closed")
    await session.commit()

    runs = (await client.get("/api/runs")).json()["data"]
    assert runs[0]["id"] == run_id
    assert runs[0]["byStatus"] == {"succeeded": 1, "failed": 1, "pending": 1}
    # The failed transfer's money is back in the fund; the run's total is what
    # was asked for, not what was paid.
    assert runs[0]["totalMinor"] == 3 * EACH
    assert await ledger_service.balance(session, account_id=funding.id) == FUND - 2 * EACH


async def test_the_transfer_list_can_be_filtered_to_one_run(client, funding, people):
    await client.post(
        "/api/transfers",
        json={"recipientId": str(people[0].id), "amountMinor": 100_00, "currency": KES},
        headers={"Idempotency-Key": "one-off-key"},
    )
    run_id = (await _post(client, _body(people[1:]))).json()["data"]["id"]

    everything = (await client.get("/api/transfers")).json()["data"]
    in_run = (await client.get(f"/api/transfers?run_id={run_id}")).json()["data"]
    assert len(everything) == 3
    assert len(in_run) == 2
    assert {t["runId"] for t in in_run} == {run_id}


async def test_two_runs_at_once_cannot_overdraw_the_fund(session, funding, people):
    """Write skew, a run at a time.

    Each run fits the fund on its own and the two together do not. Without the
    lock taken before the total is checked, both read the balance, both see
    enough, and both post — every journal balanced, the fund overdrawn.
    """

    async def attempt(recipients) -> str:
        async with SessionFactory() as s:
            try:
                await run_service.initiate(
                    s,
                    items=[RunItem(r.id, 3_000_00) for r in recipients],
                    currency=KES,
                )
                await s.commit()
            except transfer_service.InsufficientFundsError:
                await s.rollback()
                return "refused"
            return "authorised"

    outcomes = await asyncio.gather(attempt(people[:2]), attempt(people[1:]))

    assert sorted(outcomes) == ["authorised", "refused"], outcomes
    assert await ledger_service.balance(session, account_id=funding.id) == FUND - 6_000_00
    assert await ledger_service.trial_balance(session) == 0
