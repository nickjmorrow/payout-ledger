"""Comparing our books against the provider's records, and saying where they differ.

Everything else in this system is built so that a single failure cannot lose or
duplicate money: idempotency keys, balanced journals, a queue that retries.
Reconciliation exists because that is not enough. It catches the failures that
happen *between* the guarantees — a send whose response we never saw, a
settlement we stopped polling for, a payment they have and we do not. Those
leave no error anywhere; the only way to find them is to compare two
independent records and notice they disagree.

**Which is why the provider's payments are their own table.** If their records
lived on our `transfers` rows, this module would be comparing a row to itself
and would always pass.

Five kinds of disagreement, and the split between what is healed and what is
only reported is the whole judgement of this file:

  status_behind        They say settled, we still say in flight. **Healed.**
                       They are authoritative about their own payments, and
                       this is just a poll we missed.
  status_contradicted  They say failed, we say succeeded (or the reverse in a
                       way that is not merely behind). **Reported.** Our books
                       already moved money on the strength of the other answer,
                       and unwinding that automatically on one disagreement is
                       how a glitch becomes a reversal storm.
  missing_at_provider  We believe we sent it; they have no record. **Reported.**
                       Either our send never landed or they lost it, and those
                       need opposite responses — re-send, or investigate. A
                       machine cannot tell which from here.
  unknown_to_us        They have a payment we cannot match to a transfer.
                       **Reported**, and the most serious: money left the float
                       without our books knowing.
  amount_mismatch      Same payment, different amount. **Reported**, always.

The rule behind that split: heal only where the provider is authoritative and
we are merely out of date. Anywhere the two records genuinely contradict each
other, a person decides.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bus import announce
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

    `since` is a window rather than "everything" because this runs repeatedly
    and forever: a full scan is affordable now and will not be, and a job whose
    cost grows with total history is one that quietly stops completing. The
    window must comfortably exceed the longest a payment can legitimately take
    to settle, or a payment still in flight looks like drift on every pass.
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
            # Still `pending`: we have not handed it over yet, so there is
            # nothing on their side to disagree with. Not drift.
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
    # After the flush, so each finding has its id. Delivered at the caller's
    # commit, in the same transaction that recorded them.
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
        # Deliberately no status comparison after this. With the amounts in
        # disagreement the statuses are not comparable anyway, and healing on
        # top of a mismatch would settle the wrong number.
        return

    if transfer.status == "processing" and payment.status in {"succeeded", "failed"}:
        # The benign case, and the common one: a settlement poll we missed.
        # They are authoritative about their own payment.
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
