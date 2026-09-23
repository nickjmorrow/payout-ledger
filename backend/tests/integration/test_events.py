"""Change notices: what the console hears, and when.

The property worth most here is *when*. A notice published before its rows are
visible would send the browser to re-read a transfer that is not there yet,
and one published on a transaction that rolls back would announce a payment
that never happened. Both are ruled out by NOTIFY being transactional, and the
tests below hold the services to publishing through it.
"""

import asyncio
import json

import pytest

from app.api.routes import events
from app.bus import EVENTS_CHANNEL, bus
from app.models import Account, Recipient
from app.services import ledger_service, task_service, transfer_service
from app.services.ledger_service import Posting

KES = "KES"


@pytest.fixture
async def listening():
    await bus.start()
    try:
        async with bus.subscribe(EVENTS_CHANNEL) as queue:
            yield queue
    finally:
        await bus.stop()


@pytest.fixture
async def recipient(session):
    funding = Account(name="Fund", kind="program_funding", currency=KES)
    settlement = Account(name="Float", kind="provider_settlement", currency=KES)
    person = Recipient(full_name="Asha Mwangi", msisdn="+254700000001", country="KE")
    session.add_all([funding, settlement, person])
    await session.flush()
    await ledger_service.post(
        session,
        kind="funding_deposit",
        currency=KES,
        postings=[
            Posting(account_id=settlement.id, direction="debit", amount_minor=100_000_00),
            Posting(account_id=funding.id, direction="credit", amount_minor=100_000_00),
        ],
    )
    await session.commit()
    return person


async def _drain(queue: asyncio.Queue, *, settle: float = 0.3) -> list[dict]:
    """Everything delivered until the channel goes quiet."""
    frames = []
    while True:
        try:
            frames.append(await asyncio.wait_for(queue.get(), timeout=settle))
        except TimeoutError:
            return frames


async def test_authorising_a_transfer_announces_it_and_its_task_at_commit(
    session, recipient, listening
):
    transfer = await transfer_service.initiate(
        session, recipient_id=recipient.id, amount_minor=2_500_00, currency=KES
    )
    assert await _drain(listening) == [], "announced before commit"

    await session.commit()
    frames = await _drain(listening)

    assert {"topic": "transfers", "id": str(transfer.id), "transferId": None} in frames
    task_frames = [f for f in frames if f["topic"] == "tasks"]
    assert len(task_frames) == 1
    assert task_frames[0]["transferId"] == str(transfer.id)


async def test_a_rolled_back_transfer_is_never_announced(session, recipient, listening):
    """A notice for a payment that never happened is the one frame that could mislead."""
    await transfer_service.initiate(
        session, recipient_id=recipient.id, amount_minor=2_500_00, currency=KES
    )
    await session.rollback()
    assert await _drain(listening) == []


async def test_a_claim_is_announced_with_the_transfer_it_belongs_to(session, listening):
    await task_service.enqueue(session, kind="noop", payload={"transfer_id": "t-1"})
    await session.commit()
    await _drain(listening)

    claimed = await task_service.claim_next(session, worker_id="w1")
    assert claimed is not None
    frames = await _drain(listening)
    assert frames == [{"topic": "tasks", "id": str(claimed.id), "transferId": "t-1"}]


# ------------------------------------------------------------------ the stream


async def test_the_stream_says_ready_only_once_it_is_listening(session):
    """`ready` is the browser's cue to re-read everything, so it must come after LISTEN.

    Sent any earlier, a change committed between the browser's refresh and the
    subscription would be seen by neither. Asserted by committing a change the
    instant `ready` arrives and requiring it on the stream.
    """
    await bus.start()
    frames = events.stream(heartbeat_seconds=5.0)
    try:
        first = await anext(frames)
        assert first.startswith("event: ready\n")

        task = await task_service.enqueue(session, kind="noop")
        await session.commit()

        change = await asyncio.wait_for(anext(frames), timeout=5.0)
        assert change.startswith("event: change\n")
        data = json.loads(change.split("data: ", 1)[1])
        assert data == {"topic": "tasks", "id": str(task.id), "transferId": None}
    finally:
        await frames.aclose()
        await bus.stop()


async def test_an_idle_stream_sends_heartbeats():
    """Bytes on an idle connection keep proxies from closing it and find dead clients."""
    await bus.start()
    frames = events.stream(heartbeat_seconds=0.1)
    try:
        await anext(frames)
        assert await asyncio.wait_for(anext(frames), timeout=2.0) == ": heartbeat\n\n"
    finally:
        await frames.aclose()
        await bus.stop()
