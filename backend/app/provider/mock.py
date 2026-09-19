"""A mobile-money provider that lives in our own database.

**This file is pretending to be somebody else's company.** It is the only
thing that may write `provider_payments`, and nothing outside `app/provider/`
may read that table — the rest of the application reaches it through
`PaymentProvider`, exactly as it would reach an HTTP API. Both rules are
checked by a structural test, because the moment a service reads their table
directly, reconciliation starts comparing our records to our records.

It runs on its own session rather than the caller's, for the same reason. A
real provider does not enlist in your transaction: it commits when it commits,
and your rollback does not unsend a payment. Sharing the caller's session would
quietly give the mock a property the real thing cannot have — and every test
written against that property would pass here and fail in production.

Payments do not settle instantly. `send_payment` records a `pending` payment
and returns; it becomes `succeeded` once it is old enough. That gap is
deliberate — a provider that succeeds synchronously never exercises the states
(in flight, settled later, never settled) the whole async design exists to
handle.

**Settlement happens lazily, inside `get_payment` and `list_payments`.** That
is not a shortcut, it is the point: a real provider settles on its own schedule
and offers no way to make it happen sooner, so nothing in this application may
be able to *cause* a settlement. If the worker had to call `advance_pending` on
a timer, the app would be driving the provider's clock — and every test written
against that ability would pass here and fail against a real integration. Time
passing is the only trigger, and asking is the only way to find out.

The failure hooks at the bottom default to off. They exist so the chaos pass
has somewhere to plug in, and are not themselves that pass.
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.db import SessionFactory
from app.logging import get_logger
from app.models import ProviderPayment
from app.provider.base import ProviderError, ProviderPaymentView

logger = get_logger(__name__)

# The prefix on every reference they hand back. Distinctive so a reference that
# turns up somewhere unexpected is obviously theirs and not ours.
REFERENCE_PREFIX = "MM"


def _view(row: ProviderPayment) -> ProviderPaymentView:
    return ProviderPaymentView(
        reference=row.reference,
        idempotency_key=row.idempotency_key,
        msisdn=row.msisdn,
        amount_minor=row.amount_minor,
        currency=row.currency,
        status=row.status,  # pyright: ignore[reportArgumentType]
        failure_reason=row.failure_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class MockProvider:
    """The default `PaymentProvider`. Satisfies the Protocol structurally."""

    async def send_payment(
        self,
        *,
        idempotency_key: str,
        msisdn: str,
        amount_minor: int,
        currency: str,
    ) -> ProviderPaymentView:
        if settings.provider_reject_all:
            raise ProviderError("provider rejecting all payments", retryable=False)
        if settings.provider_unreachable:
            raise ProviderError("provider unreachable", retryable=True)

        async with SessionFactory() as session:
            # Their dedupe, not ours. A repeat of the same key returns the
            # original payment rather than making a second one, which is the
            # property that makes our retries safe.
            existing = await session.execute(
                select(ProviderPayment).where(ProviderPayment.idempotency_key == idempotency_key)
            )
            if (row := existing.scalar_one_or_none()) is not None:
                logger.info(
                    "provider replayed payment",
                    reference=row.reference,
                    idempotency_key=idempotency_key,
                )
                return _view(row)

            payment = ProviderPayment(
                reference=f"{REFERENCE_PREFIX}{secrets.token_hex(8).upper()}",
                idempotency_key=idempotency_key,
                msisdn=msisdn,
                amount_minor=amount_minor,
                currency=currency,
                status="pending",
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)

            logger.info(
                "provider accepted payment",
                reference=payment.reference,
                msisdn=msisdn,
                amount_minor=amount_minor,
            )
            return _view(payment)

    async def get_payment(self, *, reference: str) -> ProviderPaymentView | None:
        # Settle first, so the answer reflects the passage of time rather than
        # whenever somebody last ran a job. See the module docstring.
        await self.advance_pending()
        async with SessionFactory() as session:
            row = await session.get(ProviderPayment, reference)
            return _view(row) if row is not None else None

    async def list_payments(self, *, since: datetime) -> list[ProviderPaymentView]:
        await self.advance_pending()
        async with SessionFactory() as session:
            result = await session.execute(
                select(ProviderPayment)
                .where(ProviderPayment.created_at >= since)
                .order_by(ProviderPayment.created_at)
            )
            return [_view(row) for row in result.scalars()]

    # ------------------------------------------------------------ mock only
    #
    # Not part of `PaymentProvider`. Called by the two reads above and by
    # tests, and by nothing else — a structural test keeps this module
    # unimportable from outside `app/provider/` precisely so that application
    # code cannot reach it.

    async def advance_pending(self) -> int:
        """Settle pending payments that are old enough. Returns how many moved.

        Stands in for the provider's own clock. Idempotent and safe to call on
        every read: it only touches rows whose settle time has passed, so
        calling it twice in a row does nothing the second time.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=settings.provider_settle_after_seconds)

        async with SessionFactory() as session:
            result = await session.execute(
                select(ProviderPayment).where(
                    ProviderPayment.status == "pending",
                    ProviderPayment.created_at <= cutoff,
                )
            )
            rows = list(result.scalars())
            for row in rows:
                if settings.provider_fail_settlement:
                    row.status = "failed"
                    row.failure_reason = "recipient wallet unreachable"
                else:
                    row.status = "succeeded"
            await session.commit()

        if rows:
            logger.info("provider settled payments", count=len(rows))
        return len(rows)
