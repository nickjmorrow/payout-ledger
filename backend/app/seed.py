"""The chart of accounts, and enough data to see the thing work.

Two different jobs in one module, deliberately kept apart below:

  **The chart of accounts is not demo data.** A programme funding account and a
  provider settlement account have to exist before any transfer can be
  authorised — `ledger_service.system_account` raises without them — so this is
  required setup, and it runs on every boot.

  **The recipients and the opening balance are demo data.** They exist so that
  a fresh `docker compose up` shows a working system rather than an empty one.
  Set `SEED_DEMO_DATA=false` to skip them and keep the chart.

**Idempotent, because it runs on every boot.** Everything here is either an
`ON CONFLICT DO NOTHING` insert or guarded by a check, so a second run is a
no-op. That matters more than it looks: the opening balance is a journal entry,
and a seeder that posted it twice would double the fund on every restart.

Run with `python -m app.seed`.
"""

import asyncio

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionFactory, engine
from app.logging import configure_logging, get_logger
from app.models import Account, JournalEntry, Recipient
from app.services import ledger_service
from app.services.ledger_service import Posting

logger = get_logger(__name__)

CURRENCY = "KES"

# The opening balance the demo programme starts with: 2,000,000 KES.
OPENING_BALANCE_MINOR = 2_000_000_00

# Names and numbers are obviously fictional. The +254 prefix is Kenya, matching
# the KES currency, so the demo reads as one coherent programme rather than a
# pile of unrelated test rows.
DEMO_RECIPIENTS = [
    ("Asha Mwangi", "+254700000101"),
    ("Brian Otieno", "+254700000102"),
    ("Caroline Wanjiru", "+254700000103"),
    ("David Kipchoge", "+254700000104"),
    ("Esther Nyambura", "+254700000105"),
    ("Francis Mutua", "+254700000106"),
]


async def seed_chart_of_accounts(session: AsyncSession) -> None:
    """The accounts every transfer needs. Required, not optional."""
    for kind, name in (
        ("program_funding", "Unconditional cash transfer programme"),
        ("provider_settlement", "Mobile money float"),
    ):
        await session.execute(
            pg_insert(Account)
            .values(name=name, kind=kind, currency=CURRENCY)
            # The unique index is partial (system accounts have a null
            # recipient), so its predicate has to be restated here or Postgres
            # will not match it.
            .on_conflict_do_nothing(
                index_elements=["kind", "currency"],
                index_where=Account.recipient_id.is_(None),
            )
        )
    await session.commit()


async def seed_demo_data(session: AsyncSession) -> None:
    """Recipients and an opening balance, so a fresh boot shows something."""
    for full_name, msisdn in DEMO_RECIPIENTS:
        await session.execute(
            pg_insert(Recipient)
            .values(full_name=full_name, msisdn=msisdn, country="KE")
            .on_conflict_do_nothing(index_elements=["msisdn"])
        )
    await session.commit()

    # Guarded rather than ON CONFLICT: a journal entry has no natural key to
    # conflict on, so the check is "has this programme ever been funded".
    # Without it every restart would post another opening balance and the fund
    # would grow on its own — which would also keep the books balanced, and so
    # would never be caught by any constraint.
    funded = await session.execute(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.kind == "funding_deposit")
    )
    if funded.scalar_one() > 0:
        logger.info("programme already funded, skipping opening balance")
        return

    funding = await ledger_service.system_account(
        session, kind="program_funding", currency=CURRENCY
    )
    settlement = await ledger_service.system_account(
        session, kind="provider_settlement", currency=CURRENCY
    )
    await ledger_service.post(
        session,
        kind="funding_deposit",
        currency=CURRENCY,
        memo="Opening balance",
        postings=[
            Posting(
                account_id=settlement.id, direction="debit", amount_minor=OPENING_BALANCE_MINOR
            ),
            Posting(account_id=funding.id, direction="credit", amount_minor=OPENING_BALANCE_MINOR),
        ],
    )
    await session.commit()
    logger.info("programme funded", amount_minor=OPENING_BALANCE_MINOR, currency=CURRENCY)


async def run() -> None:
    async with SessionFactory() as session:
        await seed_chart_of_accounts(session)
        if settings.seed_demo_data:
            await seed_demo_data(session)
        else:
            logger.info("demo data disabled, chart of accounts only")
    await engine.dispose()


def main() -> None:
    configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
