"""A payment provider that lives in our own database, standing in for an external API.

The only writer of `provider_payments`. It uses its own session, as a real
provider would not share our transaction, and settles lazily on time passing,
so nothing in the application can cause a settlement. The failure hooks in
config.py default to off. See AGENTS.md > The provider seam.
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

# Makes their references recognizable as theirs.
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
            # Their dedupe: a repeated key returns the original payment.
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
        # Settle first, so the answer reflects the time that has passed.
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
    # Not part of `PaymentProvider`. A structural test keeps this module out of
    # reach of application code.

    async def advance_pending(self) -> int:
        """Settle pending payments old enough to settle. Idempotent. Returns how many moved."""
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
