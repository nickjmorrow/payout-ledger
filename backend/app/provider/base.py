"""The seam: what a payment provider is, as far as this application is concerned.

Everything above this talks to `PaymentProvider`, never to a concrete provider.
Three operations: `send_payment` (idempotent on the key we supply),
`get_payment` (to find out what happened), and `list_payments` (for
reconciliation). See AGENTS.md > The provider seam.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

PaymentStatus = Literal["pending", "succeeded", "failed"]


@dataclass(frozen=True)
class ProviderPaymentView:
    """Their record of one payment, as we are allowed to see it.

    The contract, deliberately separate from the mock's `ProviderPayment` row.
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

    `retryable` is the adapter's judgment: only it knows whether a failure is a
    blip worth retrying or a refusal that never will succeed.
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
        """Instruct a payment. A repeated key returns the original payment."""
        ...

    async def get_payment(self, *, reference: str) -> ProviderPaymentView | None:
        """Their current view of one payment, or None if they have never heard of it."""
        ...

    async def list_payments(self, *, since: datetime) -> list[ProviderPaymentView]:
        """Everything they recorded at or after `since`. For reconciliation."""
        ...
