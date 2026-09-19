"""What a mobile-money provider is, as far as this application is concerned.

**The seam.** Everything above this file talks to `PaymentProvider` and never
to a concrete provider, which is what lets the mock be swapped for a real
integration without touching the worker, the services or the routes. A test
passes a fake in rather than patching one out.

The Protocol is deliberately small. Three operations are all a disbursement
system needs from a provider, and every one of them is on this list because
something in the design depends on it:

  - `send_payment` takes an idempotency key, because retries are mandatory and
    a retry without one is a second payment;
  - `get_payment` exists because a send whose response we never saw is not a
    send that did not happen, and asking is the only way to find out;
  - `list_payments` exists for reconciliation, which needs their whole view of
    a window rather than one payment at a time.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

PaymentStatus = Literal["pending", "succeeded", "failed"]


@dataclass(frozen=True)
class ProviderPaymentView:
    """Their record of one payment, as we are allowed to see it.

    Frozen, and a separate type from the `ProviderPayment` row on purpose. The
    row is the mock's private storage; this is the contract. A real provider
    returns JSON, not a SQLAlchemy model, and code that reads a model here
    would stop compiling the day the mock is replaced — which is the seam
    leaking.
    """

    reference: str
    idempotency_key: str
    msisdn: str
    amount_minor: int
    currency: str
    status: PaymentStatus
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class ProviderError(Exception):
    """The provider could not be reached, or refused the request.

    `retryable` is the distinction the worker acts on, and it is the provider
    adapter's job to make it — only the adapter knows whether a given failure
    is a timeout worth another attempt or a rejection that will never succeed.
    Getting this wrong in the safe-looking direction (everything retryable) is
    how a permanently invalid payment consumes its whole retry budget; getting
    it wrong the other way drops a payment because of a blip.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class PaymentProvider(Protocol):
    async def send_payment(
        self,
        *,
        idempotency_key: str,
        msisdn: str,
        amount_minor: int,
        currency: str,
    ) -> ProviderPaymentView:
        """Instruct a payment. Safe to call twice with the same key.

        The provider dedupes on `idempotency_key` and returns the original
        payment for a repeat, which is what makes a retry after an uncertain
        response safe rather than a second disbursement.
        """
        ...

    async def get_payment(self, *, reference: str) -> ProviderPaymentView | None:
        """Their current view of one payment, or None if they have never heard of it."""
        ...

    async def list_payments(self, *, since: datetime) -> list[ProviderPaymentView]:
        """Everything they recorded at or after `since`. For reconciliation."""
        ...
