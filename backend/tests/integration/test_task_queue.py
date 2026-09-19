"""The queue: claiming, scheduling, cancelling, sweeping.

These need a real Postgres. `for update skip locked` has no meaning without
concurrent transactions, and it is the property the whole worker design rests
on — if it ever silently stops holding, two workers run the same task twice.
For work that moves money, that is the difference between a disbursement and a
double payment, so this is the test to keep honest above all the others.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db import SessionFactory
from app.services import task_service


async def test_two_open_transactions_claim_different_rows(session):
    """The skip-locked test, written so that a regression fails instead of hangs.

    `asyncio.gather(claim_next(), claim_next())` would pass even without SKIP
    LOCKED, because claim_next commits before returning — it proves "two
    different rows" but not the property. Holding the first transaction open is
    what makes the second claim either succeed immediately (correct) or block
    forever (broken), and the timeout turns that hang into a readable failure.
    """
    first = await task_service.enqueue(session, kind="noop")
    second = await task_service.enqueue(session, kind="noop")

    async with SessionFactory() as s1, SessionFactory() as s2:
        claimed_a = (await s1.execute(task_service._CLAIM, {"worker_id": "w1"})).scalar()

        claimed_b = (
            await asyncio.wait_for(
                s2.execute(task_service._CLAIM, {"worker_id": "w2"}), timeout=5.0
            )
        ).scalar()

        assert claimed_a is not None
        assert claimed_b is not None
        assert {claimed_a, claimed_b} == {first.id, second.id}

        await s1.commit()
        await s2.commit()


async def test_a_task_scheduled_for_later_is_invisible_to_a_claim(session):
    """`run_at` is the entire scheduling mechanism."""
    await task_service.enqueue(
        session,
        kind="agent_run",
        run_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert await task_service.claim_next(session, worker_id="w") is None


async def test_sweep_requeues_an_abandoned_task_then_fails_it_at_max_attempts(session):
    """The sweeper is the only thing that notices a worker that died.

    Nothing in Postgres knows a worker existed, so without this a killed process
    leaves a row marked `running` forever.
    """
    task = await task_service.enqueue(session, kind="noop")
    await session.execute(
        text(
            "update tasks set status='running', attempts=1, claimed_at=now() - interval '1 hour'"
            " where id = :id"
        ),
        {"id": task.id},
    )
    await session.commit()

    assert await task_service.sweep_stale(session, stale_seconds=60) == 1
    await session.refresh(task)
    assert task.status == "pending"

    # Out of attempts: the next sweep gives up rather than looping forever.
    await session.execute(
        text(
            "update tasks set status='running', attempts=3, max_attempts=3,"
            " claimed_at=now() - interval '1 hour' where id = :id"
        ),
        {"id": task.id},
    )
    await session.commit()

    assert await task_service.sweep_stale(session, stale_seconds=60) == 1
    await session.refresh(task)
    assert task.status == "failed"
    assert task.error == "worker died mid-task"


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [(1, 5.0), (2, 10.0), (3, 20.0), (10, 120.0)],
)
def test_retry_backoff_doubles_and_caps(attempts: int, expected: float):
    assert task_service.retry_delay_seconds(attempts) == expected


async def test_release_returns_a_claimed_task_without_counting_it_as_failed(session):
    task = await task_service.enqueue(session, kind="noop")
    claimed = await task_service.claim_next(session, worker_id="w1")
    assert claimed is not None
    assert claimed.attempts == 1

    await task_service.release(session, task=claimed)
    await session.refresh(task)
    assert task.status == "pending"
    assert task.claimed_by is None
    # Not decremented: a task handed around forever should still run out.
    assert task.attempts == 1


async def test_enqueue_defaults_come_from_the_database_not_just_the_orm(session):
    """Raw inserts must work too — task_service's own SQL does not go via the ORM."""
    await session.execute(text("insert into tasks (kind) values ('noop')"))
    await session.commit()
    row = (
        await session.execute(
            text("select status, attempts, max_attempts, cancel_requested, payload from tasks")
        )
    ).first()
    assert row is not None
    assert row.status == "pending"
    assert (row.attempts, row.max_attempts, row.cancel_requested) == (0, 3, False)
    assert row.payload == {}


async def test_check_constraints_are_enforced_by_the_database(session):
    """Declared on the models so Alembic emits them; asserted here so they stay.

    They exist only because models.py declares them — the drift that made this
    worth checking is that they once existed only in hand-written SQL, and
    autogenerating against the models would have dropped them silently.

    There is deliberately no equivalent for `kind`. A task kind is a handler
    registration, not a schema fact, and a CHECK that had to be migrated every
    time one was added would be a CHECK people route around.
    """
    with pytest.raises(Exception, match="tasks_status_check"):
        await session.execute(text("insert into tasks (kind, status) values ('noop', 'nonsense')"))
    await session.rollback()


async def test_cancel_requested_is_visible_to_the_process_running_the_task(session):
    """Cancellation is cooperative, so it has to travel through the database.

    The worker is a different process and cannot be interrupted; all a stop can
    do is set a flag that the handler reads between units of work. A row that
    has vanished counts as cancelled too — there is nothing left to settle, and
    carrying on can only write orphans.
    """
    task = await task_service.enqueue(session, kind="noop")
    task.status = "running"
    await session.commit()
    assert await task_service.cancel_requested(session, task_id=task.id) is False

    await task_service.request_cancel(session, task=task)
    assert await task_service.cancel_requested(session, task_id=task.id) is True

    assert await task_service.cancel_requested(session, task_id=uuid.uuid4()) is True


async def test_a_pending_task_is_cancelled_outright(session):
    """Nothing has claimed it, so there is no worker to cooperate with."""
    task = await task_service.enqueue(session, kind="noop")
    await task_service.request_cancel(session, task=task)
    assert task.status == "cancelled"
    assert await task_service.claim_next(session, worker_id="w") is None
