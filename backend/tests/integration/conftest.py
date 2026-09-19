"""The database, for the tests that need one.

Isolation is one disposable database per test session, with a TRUNCATE between
tests. Not a transaction rolled back per test, which is faster but would defeat
the four things most worth testing here:

* the worker and the streaming endpoint open their own sessions from the global
  `SessionFactory`, on a different connection, and would never see uncommitted
  rows;
* `claim_next` is `for update skip locked`, which needs two concurrent
  transactions to mean anything, and inside one outer transaction there is only
  ever one;
* `pg_notify` delivers at COMMIT, so in a transaction that always rolls back no
  notification is ever delivered and every bus test silently sees nothing.

TRUNCATE on empty tables costs a millisecond or two. That is the whole saving
being passed up, and it buys back the tests that matter.

These fixtures live here rather than in `tests/conftest.py` because autouse
reaches downward only. `tests/unit` and `tests/structure` sit beside this
directory, not below it, so they run with no Postgres anywhere in sight.
"""

import asyncio
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.db import SessionFactory, engine
from tests.conftest import ADMIN_DSN, TEST_DB

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# CASCADE resolves the FK order for us, so this list does not have to be
# topologically sorted — but every table does have to be named, or a test that
# leaves rows behind silently poisons the next one.
TABLES = (
    "tasks, recipients, accounts, transfers, journal_entries, ledger_entries,"
    " idempotency_keys, provider_payments"
)


def _migrate() -> None:
    """Build the schema the same way production does.

    `alembic upgrade head`, not `Base.metadata.create_all()`. They are supposed
    to agree, and testing against the one that is not deployed is how you find
    out they stopped agreeing only after a release.
    """
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
async def _database():
    assert TEST_DB.startswith("test_"), "refusing to drop a database not named test_*"

    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'drop database if exists "{TEST_DB}" with (force)')
    await admin.execute(f'create database "{TEST_DB}"')
    await admin.close()

    # In a thread, because alembic/env.py ends in `asyncio.run(...)` — Alembic's
    # entry point is synchronous by design, and this fixture is already inside a
    # running loop. A fresh thread has no loop for it to collide with.
    await asyncio.to_thread(_migrate)
    yield

    await engine.dispose()
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'drop database if exists "{TEST_DB}" with (force)')
    await admin.close()


@pytest.fixture(autouse=True)
async def _clean_between_tests():
    yield
    # RESTART IDENTITY so any identity column a future table adds restarts at
    # 1, and a test can assert on a sequence value rather than on whatever the
    # tests before it happened to leave behind.
    async with SessionFactory() as session:
        await session.execute(text(f"truncate {TABLES} restart identity cascade"))
        await session.commit()


@pytest.fixture
async def session():
    """A session for the test body.

    Committing in a test is fine and expected: the code under test commits, and
    the worker and streaming paths open their own sessions that must be able to
    see the result.
    """
    async with SessionFactory() as session:
        yield session
