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

CURRENCY = "USD"

# The opening balance the demo program starts with: $1,000,000.
OPENING_BALANCE_MINOR = 1_000_000_00

# Names and numbers are obviously fictional. The numbers are in 555-0100
# through 555-0199, the block reserved for fiction, so none of them can ring
# anybody — and all are +1, matching the currency, so the demo reads as one
# coherent program rather than a pile of unrelated test rows.
#
# Twenty-four rather than a handful so a payment run is a real batch: enough
# transfers that the queue visibly works through them, and that two workers
# claiming with SKIP LOCKED take different ones. Appending here is safe on a
# database seeded with fewer — the insert below is ON CONFLICT DO NOTHING on
# the number, so the originals stay and only the new ones arrive.
DEMO_RECIPIENTS = [
    ("Ava Johnson", "+12025550101"),
    ("Brandon Lee", "+12025550102"),
    ("Carmen Rodriguez", "+12025550103"),
    ("Darnell Washington", "+12025550104"),
    ("Emily Nguyen", "+12025550105"),
    ("Frank Miller", "+12025550106"),
    ("Gabriela Torres", "+12025550107"),
    ("Henry Davis", "+12025550108"),
    ("Isabella Martinez", "+12025550109"),
    ("Jamal Carter", "+12025550110"),
    ("Katie O'Brien", "+12025550111"),
    ("Luis Hernandez", "+12025550112"),
    ("Maya Patel", "+12025550113"),
    ("Nathan Brooks", "+12025550114"),
    ("Olivia Kim", "+12025550115"),
    ("Paul Anderson", "+12025550116"),
    ("Rosa Jimenez", "+12025550117"),
    ("Samuel Wright", "+12025550118"),
    ("Tanya Robinson", "+12025550119"),
    ("Victor Chen", "+12025550120"),
    ("Whitney Scott", "+12025550121"),
    ("Xavier Price", "+12025550122"),
    ("Yolanda Bell", "+12025550123"),
    ("Zachary Cooper", "+12025550124"),
]


async def seed_chart_of_accounts(session: AsyncSession) -> None:
    """The accounts every transfer needs. Required, not optional."""
    for kind, name in (
        ("program_funding", "Unconditional cash transfer program"),
        ("provider_settlement", "Provider float"),
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
            .values(full_name=full_name, msisdn=msisdn, country="US")
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
