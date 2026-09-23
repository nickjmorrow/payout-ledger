"""The chart of accounts, and demo data.

The chart of accounts is required setup: no transfer can be authorized
without it. The recipients and opening balance are demo data, skipped when
`SEED_DEMO_DATA=false`. Idempotent, because it runs on every boot.

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

# Fictional names, and numbers from 555-0100 to 555-0199, the block reserved
# for fiction. Twenty-four, so a payment run is a real batch.
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
            # The unique index is partial; Postgres only matches it if the predicate is restated.
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

    # Guarded rather than ON CONFLICT, because a journal has no natural key. A
    # second opening balance would balance, so no constraint would catch it.
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
