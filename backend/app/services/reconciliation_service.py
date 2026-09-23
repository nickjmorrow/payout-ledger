"""Comparing our books against the provider's records, and saying where they differ.

Heal only where the provider is authoritative and we are merely out of date
(`status_behind`); report everything else for a person to decide. See
AGENTS.md > Reconciliation for what each kind of finding means.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Text, cast, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import announce
from app.config import settings
from app.logging import get_logger
from app.models import ReconciliationFinding, Transfer
from app.provider.base import PaymentProvider, ProviderPaymentView
from app.services import transfer_service

logger = get_logger(__name__)

# Statuses we consider "we think the provider has this".
IN_FLIGHT = frozenset({"processing", "succeeded"})


@dataclass
class Report:
    """What one pass found. Returned for logging and for the operations view."""

    checked: int = 0
    healed: int = 0
    findings: list[ReconciliationFinding] = field(default_factory=list)

    @property
    def unresolved(self) -> int:
        return len(self.findings) - self.healed


async def run(
    session: AsyncSession,
    *,
    provider: PaymentProvider,
    since: datetime | None = None,
) -> Report:
    """Compare both sides over a window and record every disagreement.

    A window, so the cost does not grow with total history. It must exceed the
    longest a payment can take to settle, or in-flight payments look like drift.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=1)

    report = Report()

    theirs = {p.reference: p for p in await provider.list_payments(since=since)}
    ours = await _our_transfers(session, since=since)

    matched: set[str] = set()

    for transfer in ours:
        report.checked += 1

        if transfer.provider_reference is None:
            # Not handed over yet, so nothing on their side to disagree with.
            continue

        payment = theirs.get(transfer.provider_reference)
        if payment is None:
            _record(
                report,
                session,
                kind="missing_at_provider",
                transfer=transfer,
                reference=transfer.provider_reference,
                detail=(
                    f"transfer is {transfer.status} against reference "
                    f"{transfer.provider_reference}, which the provider has no record of. "
                    "Either the send never landed or they lost it; those need opposite "
                    "responses, so this is not healed automatically."
                ),
            )
            continue

        matched.add(payment.reference)
        await _compare(report, session, transfer=transfer, payment=payment)

    for reference, payment in theirs.items():
        if reference in matched:
            continue
        if await _transfer_for(session, reference=reference) is not None:
            # Ours, but outside the transfer window — not an orphan.
            continue
        _record(
            report,
            session,
            kind="unknown_to_us",
            transfer=None,
            reference=reference,
            detail=(
                f"provider holds payment {reference} for {payment.amount_minor} "
                f"{payment.currency} to {payment.msisdn}, which matches no transfer. "
                "Money has left the float without the books knowing."
            ),
        )

    await session.flush()
    # After the flush, so each finding has its id.
    for finding in report.findings:
        await announce(
            session, topic="findings", subject_id=finding.id, transfer_id=finding.transfer_id
        )
    logger.info(
        "reconciliation complete",
        checked=report.checked,
        findings=len(report.findings),
        healed=report.healed,
        unresolved=report.unresolved,
    )
    return report


async def _compare(
    report: Report,
    session: AsyncSession,
    *,
    transfer: Transfer,
    payment: ProviderPaymentView,
) -> None:
    """One transfer against the provider's record of it."""
    if payment.amount_minor != transfer.amount_minor or payment.currency != transfer.currency:
        _record(
            report,
            session,
            kind="amount_mismatch",
            transfer=transfer,
            reference=payment.reference,
            detail=(
                f"we recorded {transfer.amount_minor} {transfer.currency}, "
                f"the provider recorded {payment.amount_minor} {payment.currency}"
            ),
        )
        # No status comparison: healing on top of a mismatch would settle the
        # wrong amount.
        return

    if transfer.status == "processing" and payment.status in {"succeeded", "failed"}:
        # A settlement poll we missed. They are authoritative about their own payment.
        finding = _record(
            report,
            session,
            kind="status_behind",
            transfer=transfer,
            reference=payment.reference,
            detail=(
                f"provider settled this as {payment.status} and our books still said "
                f"{transfer.status}; brought into line"
            ),
        )
        if payment.status == "succeeded":
            await transfer_service.mark_succeeded(session, transfer=transfer)
        else:
            await transfer_service.mark_failed(
                session,
                transfer=transfer,
                reason=payment.failure_reason or "provider reported failure",
            )
        finding.healed = True
        report.healed += 1
        return

    if transfer.status == "succeeded" and payment.status == "failed":
        _record(
            report,
            session,
            kind="status_contradicted",
            transfer=transfer,
            reference=payment.reference,
            detail=(
                "our books say this recipient was paid and the provider says the payment "
                "failed. The settlement journal has already moved money, so this is not "
                "reversed automatically — a person decides."
            ),
        )
    elif transfer.status == "failed" and payment.status == "succeeded":
        _record(
            report,
            session,
            kind="status_contradicted",
            transfer=transfer,
            reference=payment.reference,
            detail=(
                "we reversed this transfer and returned the money to the fund, but the "
                "provider says the recipient was paid. The program has paid out money "
                "its books show as still available."
            ),
        )


def _record(
    report: Report,
    session: AsyncSession,
    *,
    kind: str,
    transfer: Transfer | None,
    reference: str | None,
    detail: str,
) -> ReconciliationFinding:
    finding = ReconciliationFinding(
        kind=kind,
        transfer_id=transfer.id if transfer is not None else None,
        provider_reference=reference,
        detail=detail,
    )
    session.add(finding)
    report.findings.append(finding)
    logger.warning(
        "reconciliation finding",
        kind=kind,
        transfer_id=str(transfer.id) if transfer else None,
        provider_reference=reference,
    )
    return finding


async def _our_transfers(session: AsyncSession, *, since: datetime) -> list[Transfer]:
    result = await session.execute(select(Transfer).where(Transfer.created_at >= since))
    return list(result.scalars())


async def _transfer_for(session: AsyncSession, *, reference: str) -> Transfer | None:
    result = await session.execute(select(Transfer).where(Transfer.provider_reference == reference))
    return result.scalar_one_or_none()


async def recent_findings(session: AsyncSession, *, limit: int = 50) -> list[ReconciliationFinding]:
    result = await session.execute(
        select(ReconciliationFinding).order_by(ReconciliationFinding.created_at.desc()).limit(limit)
    )
    return list(result.scalars())


async def currently_reported(session: AsyncSession) -> int:
    """Distinct unhealed problems reported within the last two passes.

    Distinct, because every pass re-reports a persisting problem; recent, so a
    problem that stops being reported leaves the count.
    """
    window = timedelta(seconds=2 * settings.reconcile_interval_seconds)
    problem = func.concat(
        ReconciliationFinding.kind,
        ":",
        func.coalesce(
            cast(ReconciliationFinding.transfer_id, Text),
            ReconciliationFinding.provider_reference,
            "",
        ),
    )
    result = await session.execute(
        select(func.count(distinct(problem))).where(
            ReconciliationFinding.healed.is_(False),
            ReconciliationFinding.created_at >= func.now() - window,
        )
    )
    return int(result.scalar_one())
