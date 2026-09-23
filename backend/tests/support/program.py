"""A funded program and its recipients, the way most integration tests start."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Recipient
from app.services import ledger_service
from app.services.ledger_service import Posting


async def open_program(
    session: AsyncSession, *, fund_minor: int = 100_000_00, currency: str = "KES"
) -> tuple[Account, Account]:
    """The program fund and the provider float, the fund opened with `fund_minor`. Committed."""
    fund = Account(name="Program fund", kind="program_funding", currency=currency)
    float_ = Account(name="Provider float", kind="provider_settlement", currency=currency)
    session.add_all([fund, float_])
    await session.flush()
    await ledger_service.post(
        session,
        kind="funding_deposit",
        currency=currency,
        postings=[
            Posting(account_id=float_.id, direction="debit", amount_minor=fund_minor),
            Posting(account_id=fund.id, direction="credit", amount_minor=fund_minor),
        ],
    )
    await session.commit()
    return fund, float_


async def enroll(
    session: AsyncSession, *, name: str = "Asha Mwangi", msisdn: str = "+254700000001"
) -> Recipient:
    """One recipient. Committed."""
    recipient = Recipient(full_name=name, msisdn=msisdn, country="KE")
    session.add(recipient)
    await session.commit()
    await session.refresh(recipient)
    return recipient
