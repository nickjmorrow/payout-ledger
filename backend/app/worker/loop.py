"""The process: claim, run, settle, wait for a NOTIFY, repeat.

Run with `python -m app.worker`. The worker is a separate process from the API
on purpose, and that separation is the point rather than an implementation
detail: because the thing doing the work cannot see the thing serving the
request, every interesting problem becomes explicit instead of implicit.
Cancellation has to travel through the database. A dead worker has to be
noticed by someone else. Work that outlives a request has to be resumable by a
process that did not serve it. Put the work back inside the request handler and
all three disappear — along with the guarantee that a dropped connection does
not cost you the job.

The loop is: sweep dead workers, drain the queue, wait for a NOTIFY. Nothing
clever, and nothing that needs to be.
"""

import asyncio
import os
import socket

from app.bus import bus
from app.config import settings
from app.db import SessionFactory, engine
from app.logging import configure_logging, get_logger
from app.services import task_service
from app.worker import shutdown
from app.worker.handlers import execute

logger = get_logger(__name__)


async def drain(worker_id: str) -> int:
    """Claim and run until the queue is empty, or until asked to stop."""
    count = 0
    while not shutdown.requested.is_set():
        async with SessionFactory() as session:
            task = await task_service.claim_next(session, worker_id=worker_id)
        if task is None:
            return count
        await execute(task)
        count += 1
    return count


async def tick(worker_id: str) -> int:
    """One pass of the loop: dead workers, then drain.

    Everything `run_forever` does except waiting — which is what makes it
    callable from a test without an infinite loop to escape from.
    """
    async with SessionFactory() as session:
        await task_service.sweep_stale(session)
    return await drain(worker_id)


async def run_forever() -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    logger.info("worker starting", worker_id=worker_id)

    shutdown.install_signal_handlers()

    await bus.start()

    try:
        async with bus.subscribe(task_service.WAKE_CHANNEL) as wake:
            while not shutdown.requested.is_set():
                await tick(worker_id)

                # Proof of a completed pass, for the healthcheck. After the
                # tick, not before: what matters is that the loop came back
                # round, not that it started.
                settings.worker_heartbeat_path.touch()

                # Woken by NOTIFY the instant something is enqueued; the timeout
                # is only so schedules and dead workers get noticed on a quiet
                # system. Shutdown wakes it too, so a SIGTERM during an idle
                # wait exits now rather than after the full interval.
                waiters = [
                    asyncio.ensure_future(wake.get()),
                    asyncio.ensure_future(shutdown.requested.wait()),
                ]
                done, pending = await asyncio.wait(
                    waiters,
                    timeout=settings.worker_idle_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    task.exception()  # retrieved so asyncio does not warn
    finally:
        await bus.stop()
        await engine.dispose()
        logger.info("worker stopped", worker_id=worker_id)


def main() -> None:
    configure_logging()
    asyncio.run(run_forever())
