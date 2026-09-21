"""Initiating a disbursement, over HTTP and through the service.

Two properties get the most attention here because they are the ones that cost
real money when they break:

  - a retried request is one payment, not two;
  - two concurrent transfers cannot overdraw the fund between them.

The second is write skew, and it is the one no constraint catches: each journal
balances perfectly and the programme has still promised money it does not have.
"""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.bus import bus
from app.db import SessionFactory
from app.main import app
from app.models import Account, Recipient
from app.services import ledger_service, transfer_service
from app.services.ledger_service import Posting

KES = "KES"
FUND = 100_000_00


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
async def chart(session):
    """A seeded chart of accounts with a funded programme."""
    funding = Account(name="Programme fund", kind="program_funding", currency=KES)
    settlement = Account(name="Provider float", kind="provider_settlement", currency=KES)
    session.add_all([funding, settlement])
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
    return funding, settlement


@pytest.fixture
async def recipient(session):
    row = Recipient(full_name="Asha Mwangi", msisdn="+254700000001", country="KE")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def _body(recipient_id, amount=2_500_00):
    return {"recipientId": str(recipient_id), "amountMinor": amount, "currency": KES}


async def _post(client, recipient_id, key="test-key-1", amount=2_500_00):
    return await client.post(
        "/api/transfers", json=_body(recipient_id, amount), headers={"Idempotency-Key": key}
    )


# ------------------------------------------------------------------ the flow


@pytest.mark.usefixtures("chart")
async def test_initiating_a_transfer_authorises_it_and_queues_the_payment(
    client, session, recipient
):
    response = await _post(client, recipient.id)
    assert response.status_code == 201

    data = response.json()["data"]
    assert data["status"] == "pending"
    assert data["amountMinor"] == 2_500_00
    assert data["recipientName"] == "Asha Mwangi"

    # The fund is debited at authorisation, before any money moves: the
    # programme must not be able to promise the same shilling twice.
    funding = await ledger_service.system_account(session, kind="program_funding", currency=KES)
    assert await ledger_service.balance(session, account_id=funding.id) == FUND - 2_500_00

    # And the payment is queued in the same transaction that authorised it.
    queued = await session.execute(
        text("select payload->>'transfer_id' from tasks where kind = 'disburse_transfer'")
    )
    assert queued.scalar_one() == data["id"]


@pytest.mark.usefixtures("chart")
async def test_the_books_balance_after_every_state_change(client, session, recipient):
    """Whatever happens to a transfer, the trial balance stays at zero."""
    response = await _post(client, recipient.id)
    transfer_id = uuid.UUID(response.json()["data"]["id"])
    assert await ledger_service.trial_balance(session) == 0

    transfer = await transfer_service.get(session, transfer_id=transfer_id)
    assert transfer is not None

    await transfer_service.mark_processing(session, transfer=transfer, provider_reference="MM1")
    await transfer_service.mark_succeeded(session, transfer=transfer)
    await session.commit()
    assert await ledger_service.trial_balance(session) == 0


@pytest.mark.usefixtures("chart")
async def test_a_failed_transfer_returns_the_money_to_the_fund(client, session, recipient):
    """Reversal is a new journal, not a deletion.

    Both the promise and its withdrawal stay in the history, which is what
    lets somebody answer "why did this balance move twice" months later.
    """
    response = await _post(client, recipient.id)
    transfer_id = uuid.UUID(response.json()["data"]["id"])

    funding = await ledger_service.system_account(session, kind="program_funding", currency=KES)
    assert await ledger_service.balance(session, account_id=funding.id) == FUND - 2_500_00

    transfer = await transfer_service.get(session, transfer_id=transfer_id)
    assert transfer is not None
    await transfer_service.mark_failed(session, transfer=transfer, reason="wallet unreachable")
    await session.commit()

    assert await ledger_service.balance(session, account_id=funding.id) == FUND
    payable = await ledger_service.payable_account(session, recipient_id=recipient.id, currency=KES)
    assert await ledger_service.balance(session, account_id=payable.id) == 0
    assert await ledger_service.trial_balance(session) == 0


# ------------------------------------------------------------- idempotency


@pytest.mark.usefixtures("chart")
async def test_a_retried_request_is_one_payment(client, session, recipient):
    """The property the endpoint exists to guarantee."""
    first = await _post(client, recipient.id, key="retry-me")
    second = await _post(client, recipient.id, key="retry-me")

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert second.json()["data"]["id"] == first.json()["data"]["id"]

    transfers = await transfer_service.recent(session)
    assert len(transfers) == 1

    funding = await ledger_service.system_account(session, kind="program_funding", currency=KES)
    assert await ledger_service.balance(session, account_id=funding.id) == FUND - 2_500_00


@pytest.mark.usefixtures("chart")
async def test_a_request_without_a_key_is_refused(client, recipient):
    """An endpoint that moves money and accepts a keyless request pays twice."""
    response = await client.post("/api/transfers", json=_body(recipient.id))
    assert response.status_code == 422


@pytest.mark.usefixtures("chart")
async def test_reusing_a_key_with_a_different_amount_is_refused(client, recipient):
    """Replaying here would tell the client a payment it never made succeeded."""
    await _post(client, recipient.id, key="same-key", amount=2_500_00)
    response = await _post(client, recipient.id, key="same-key", amount=9_999_00)
    assert response.status_code == 422
    assert "different request body" in response.json()["detail"]


# ----------------------------------------------------------------- refusals


@pytest.mark.usefixtures("chart")
async def test_a_transfer_larger_than_the_fund_is_refused(client, recipient):
    response = await _post(client, recipient.id, amount=FUND + 1)
    assert response.status_code == 422
    assert "programme fund holds" in response.json()["detail"]


@pytest.mark.usefixtures("chart")
async def test_an_unknown_recipient_is_a_404(client):
    response = await _post(client, uuid.uuid4())
    assert response.status_code == 404


@pytest.mark.usefixtures("chart")
async def test_a_non_positive_amount_is_refused_before_any_money_moves(client, recipient):
    response = await _post(client, recipient.id, amount=0)
    assert response.status_code == 422


# ---------------------------------------------------------------- write skew


async def test_concurrent_transfers_cannot_overdraw_the_fund(session, recipient):
    """Write skew: the failure no constraint catches.

    Two transfers, each for more than half the fund, started at the same time.
    Without the row lock in `initiate` both read the balance, both see enough
    and both post — every journal balances, the trial balance is zero, and the
    programme has promised money it does not have.

    Asserted on the resulting balance rather than on which one failed, because
    either may win. What must never happen is both succeeding.
    """
    funding = Account(name="Small fund", kind="program_funding", currency=KES)
    settlement = Account(name="Float", kind="provider_settlement", currency=KES)
    session.add_all([funding, settlement])
    await session.flush()
    await ledger_service.post(
        session,
        kind="funding_deposit",
        currency=KES,
        postings=[
            Posting(account_id=settlement.id, direction="debit", amount_minor=1_000_00),
            Posting(account_id=funding.id, direction="credit", amount_minor=1_000_00),
        ],
    )
    await session.commit()

    async def attempt() -> str:
        async with SessionFactory() as s:
            try:
                await transfer_service.initiate(
                    s, recipient_id=recipient.id, amount_minor=600_00, currency=KES
                )
                await s.commit()
            except transfer_service.InsufficientFundsError:
                await s.rollback()
                return "refused"
            return "authorised"

    outcomes = await asyncio.gather(attempt(), attempt())

    assert sorted(outcomes) == ["authorised", "refused"], outcomes
    remaining = await ledger_service.balance(session, account_id=funding.id)
    assert remaining == 400_00
    assert await ledger_service.trial_balance(session) == 0


# ------------------------------------------------------------------ the detail


@pytest.mark.usefixtures("chart")
async def test_the_detail_shows_the_journal_and_the_attempts(client, session, recipient):
    """What the books say and what the worker did, in one response.

    Asserted on what the lines *mean* rather than on their existence: the
    authorisation debits the fund and credits the recipient's payable, and a
    reversal is a second journal with the pair the other way round — both
    still on the page, because that is the whole argument for append-only.
    """
    response = await _post(client, recipient.id)
    transfer_id = response.json()["data"]["id"]

    detail = (await client.get(f"/api/transfers/{transfer_id}")).json()["data"]
    assert detail["status"] == "pending"
    assert [j["kind"] for j in detail["journals"]] == ["transfer_authorized"]
    assert [t["kind"] for t in detail["tasks"]] == ["disburse_transfer"]
    assert detail["tasks"][0]["transferId"] == transfer_id

    lines = {line["accountKind"]: line for line in detail["journals"][0]["lines"]}
    assert lines["program_funding"]["direction"] == "debit"
    assert lines["recipient_payable"]["direction"] == "credit"
    assert {line["amountMinor"] for line in lines.values()} == {2_500_00}

    # The fixture's `refresh` auto-began a transaction on this session that is
    # still open, and Postgres's `now()` is frozen at transaction start — so a
    # reversal posted through it would carry a timestamp from *before* the
    # authorisation the API just committed, and sort ahead of it. End it first.
    await session.rollback()
    transfer = await transfer_service.get(session, transfer_id=uuid.UUID(transfer_id))
    assert transfer is not None
    await transfer_service.mark_failed(session, transfer=transfer, reason="wallet unreachable")
    await session.commit()

    detail = (await client.get(f"/api/transfers/{transfer_id}")).json()["data"]
    assert [j["kind"] for j in detail["journals"]] == ["transfer_authorized", "transfer_reversed"]
    reversal = {line["accountKind"]: line for line in detail["journals"][1]["lines"]}
    assert reversal["program_funding"]["direction"] == "credit"
    assert reversal["recipient_payable"]["direction"] == "debit"


@pytest.mark.usefixtures("chart")
async def test_an_unknown_transfer_detail_is_a_404(client):
    response = await client.get(f"/api/transfers/{uuid.uuid4()}")
    assert response.status_code == 404
