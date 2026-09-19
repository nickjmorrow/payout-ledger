"""The mock provider, tested as if it were somebody else's company.

Everything here goes through `PaymentProvider`'s three operations. The one
exception is `advance_pending`, which is the mock standing in for the
provider's own clock — a real provider settles on its own schedule and offers
nothing like it.

The property worth proving is the dedupe: `send_payment` called twice with one
idempotency key must be one payment. Every retry the worker makes rests on it,
and without it at-least-once delivery means at-least-once *payment*.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.provider.base import ProviderError
from app.provider.mock import MockProvider

KES = "KES"


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def settle_immediately(monkeypatch):
    """Collapse the provider's settlement delay so a test need not wait."""
    monkeypatch.setattr(settings, "provider_settle_after_seconds", 0)


async def _send(provider: MockProvider, key: str = "key-1", amount: int = 2_500_00):
    return await provider.send_payment(
        idempotency_key=key, msisdn="+254700000001", amount_minor=amount, currency=KES
    )


async def test_a_payment_is_accepted_as_pending_not_settled(provider):
    """Instructing a payment is not the same as the money arriving.

    A provider that succeeded synchronously would never exercise the in-flight
    states the async design exists to handle, so this gap is deliberate.
    """
    payment = await _send(provider)
    assert payment.status == "pending"
    assert payment.reference.startswith("MM")
    assert payment.amount_minor == 2_500_00


async def test_the_same_idempotency_key_is_one_payment(provider):
    """The property every retry in this system rests on.

    Without it, the worker retrying a send whose response it never saw is a
    second disbursement — which is the failure this whole design is arranged
    to prevent.
    """
    first = await _send(provider, key="same-key")
    second = await _send(provider, key="same-key")

    assert second.reference == first.reference
    assert second.created_at == first.created_at

    everyone = await provider.list_payments(since=datetime.now(UTC) - timedelta(hours=1))
    assert len(everyone) == 1


async def test_a_different_key_is_a_different_payment(provider):
    first = await _send(provider, key="key-a")
    second = await _send(provider, key="key-b")
    assert first.reference != second.reference


async def test_a_payment_settles_once_it_is_old_enough(provider, settle_immediately):
    payment = await _send(provider)
    assert await provider.advance_pending() == 1

    settled = await provider.get_payment(reference=payment.reference)
    assert settled is not None
    assert settled.status == "succeeded"

    # Idempotent: a second pass finds nothing left to move.
    assert await provider.advance_pending() == 0


async def test_a_payment_is_not_settled_before_its_time(provider, monkeypatch):
    """The delay is real, not decorative."""
    monkeypatch.setattr(settings, "provider_settle_after_seconds", 3600)
    payment = await _send(provider)

    assert await provider.advance_pending() == 0
    still_pending = await provider.get_payment(reference=payment.reference)
    assert still_pending is not None
    assert still_pending.status == "pending"


async def test_an_unknown_reference_is_none_rather_than_an_error(provider):
    """Asking about a payment they have never heard of is a normal answer.

    It is what the worker gets when a send failed before reaching them, and
    treating it as an error would turn "no payment was made" into an exception
    on the path that exists to establish exactly that.
    """
    assert await provider.get_payment(reference="MMDOESNOTEXIST") is None


async def test_list_payments_is_bounded_by_time(provider):
    await _send(provider, key="in-window")
    assert len(await provider.list_payments(since=datetime.now(UTC) - timedelta(hours=1))) == 1
    assert len(await provider.list_payments(since=datetime.now(UTC) + timedelta(hours=1))) == 0


# --------------------------------------------------------------- the hooks


async def test_an_unreachable_provider_fails_retryably(provider, monkeypatch):
    """A blip. Trying again is the right response."""
    monkeypatch.setattr(settings, "provider_unreachable", True)

    with pytest.raises(ProviderError) as caught:
        await _send(provider)
    assert caught.value.retryable is True


async def test_a_rejecting_provider_fails_permanently(provider, monkeypatch):
    """Not a blip. Retrying can only burn the budget and delay the failure."""
    monkeypatch.setattr(settings, "provider_reject_all", True)

    with pytest.raises(ProviderError) as caught:
        await _send(provider)
    assert caught.value.retryable is False


async def test_a_payment_can_fail_at_settlement(provider, monkeypatch, settle_immediately):
    """The nastiest failure: accepted, then lost. The money looked on its way."""
    monkeypatch.setattr(settings, "provider_fail_settlement", True)
    payment = await _send(provider)

    await provider.advance_pending()
    failed = await provider.get_payment(reference=payment.reference)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_reason == "recipient wallet unreachable"
