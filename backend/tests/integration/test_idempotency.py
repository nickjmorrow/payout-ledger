"""Idempotency keys, including the concurrent case that is the whole point.

A single-threaded test of "second call replays the first" would pass against a
naive SELECT-then-INSERT implementation, which has a window where two requests
both see nothing and both do the work. The test that matters is the one with
two real transactions open at once, and it is at the bottom.
"""

import asyncio

import pytest

from app.db import SessionFactory
from app.services import idempotency_service
from app.services.idempotency_service import (
    InFlightError,
    KeyConflictError,
    Proceed,
    Replay,
)

ENDPOINT = "POST /transfers"
BODY = {"recipientId": "r-1", "amountMinor": 2_500_00, "currency": "KES"}


async def _claim(session, key="key-1", body=None, endpoint=ENDPOINT):
    return await idempotency_service.claim(
        session, key=key, endpoint=endpoint, body=body if body is not None else BODY
    )


async def test_a_new_key_proceeds(session):
    assert isinstance(await _claim(session), Proceed)


async def test_a_completed_key_replays_instead_of_doing_the_work_again(session):
    await _claim(session)
    await idempotency_service.record_response(
        session, key="key-1", status=201, body={"id": "t-1", "status": "pending"}
    )
    await session.commit()

    replay = await _claim(session)
    assert isinstance(replay, Replay)
    assert replay.status == 201
    assert replay.body == {"id": "t-1", "status": "pending"}


async def test_a_key_still_in_flight_is_told_to_retry_not_guessed_at(session):
    """The honest answer is "ask again", not a prediction of the other result."""
    await _claim(session)
    await session.commit()

    with pytest.raises(InFlightError, match="still being processed"):
        await _claim(session)


async def test_reusing_a_key_for_a_different_body_is_refused(session):
    """Replaying the first response here would silently discard this request.

    Which is precisely the failure an idempotency key exists to prevent: the
    client would be told its payment succeeded when the payment it actually
    asked for was never made.
    """
    await _claim(session)
    await idempotency_service.record_response(session, key="key-1", status=201, body={})
    await session.commit()

    with pytest.raises(KeyConflictError, match="different request body"):
        await _claim(session, body={**BODY, "amountMinor": 9_999_00})


async def test_a_key_is_scoped_to_its_endpoint(session):
    """A transfer's key must not come back as the answer to a refund."""
    await _claim(session)
    await idempotency_service.record_response(session, key="key-1", status=201, body={})
    await session.commit()

    with pytest.raises(KeyConflictError, match="was used for"):
        await _claim(session, endpoint="POST /refunds")


async def test_the_fingerprint_ignores_key_order_and_whitespace(session):
    """Two clients serialising the same request must agree that it is the same."""
    assert idempotency_service.fingerprint({"a": 1, "b": 2}) == idempotency_service.fingerprint(
        {"b": 2, "a": 1}
    )
    assert idempotency_service.fingerprint({"a": 1}) != idempotency_service.fingerprint({"a": 2})


async def test_a_rolled_back_claim_frees_the_key(session):
    """The key and the work are one transaction, so a failure must release it.

    Otherwise a request that died halfway would permanently block the retry
    that would have completed it — a key leak that looks exactly like a stuck
    payment.
    """
    await _claim(session)
    await session.rollback()

    assert isinstance(await _claim(session), Proceed)


async def test_two_concurrent_requests_resolve_to_one_winner(session):
    """The test this module exists for.

    Two transactions race for one key. Postgres makes the second INSERT wait on
    the first's uncommitted row rather than letting both through, so exactly
    one gets `Proceed` — and the loser, once the winner commits, is told the
    key is taken rather than being allowed to pay again.

    A SELECT-then-INSERT implementation passes every other test in this file
    and fails this one, which is why it is here.
    """
    started = asyncio.Event()

    async def winner() -> str:
        async with SessionFactory() as s:
            outcome = await _claim(s, key="race")
            started.set()
            # Hold the transaction open so the loser is forced to wait on it.
            await asyncio.sleep(0.3)
            await idempotency_service.record_response(s, key="race", status=201, body={"won": True})
            await s.commit()
            return type(outcome).__name__

    async def loser() -> str:
        await started.wait()
        async with SessionFactory() as s:
            try:
                outcome = await _claim(s, key="race")
            except InFlightError:
                return "InFlightError"
            return type(outcome).__name__

    first, second = await asyncio.gather(winner(), loser())

    assert first == "Proceed"
    # The loser either replays the winner's committed response or is told to
    # retry — never Proceed, which would be a second payment.
    assert second in {"Replay", "InFlightError"}, second


async def test_recording_against_an_unclaimed_key_is_an_error(session):
    """A response with no key behind it means the claim was lost. Say so."""
    with pytest.raises(idempotency_service.IdempotencyError, match="no idempotency key"):
        await idempotency_service.record_response(session, key="never-claimed", status=201, body={})
