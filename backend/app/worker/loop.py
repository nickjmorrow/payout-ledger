"""The worker process: sweep dead workers, drain the queue, wait for a NOTIFY.

Run with `python -m app.worker`. A separate process from the API on purpose;
see AGENTS.md > The worker.
"""

import asyncio
import os
import socket

from app.bus import bus
from app.config import settings
from app.db import SessionFactory, engine
from app.logging import configure_logging, get_logger
from app.services import task_service
from app.worker import disburse, reconcile, shutdown
from app.worker.handlers import HANDLERS, execute

logger = get_logger(__name__)

# Imported for their `@register` side effects. Without them, tasks fail as an
# "unknown task kind". Named here so the import is not removed as unused.
HANDLER_MODULES = (disburse, reconcile)


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
    """One pass of the loop, without the waiting, so a test can call it."""
    async with SessionFactory() as session:
        await task_service.sweep_stale(session)
        # Every pass, so a lost reconcile chain restarts itself.
        await reconcile.ensure_scheduled(session)
    return await drain(worker_id)


async def run_forever() -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    logger.info("worker starting", worker_id=worker_id, kinds=sorted(HANDLERS))

    shutdown.install_signal_handlers()

    await bus.start()

    try:
        async with bus.subscribe(task_service.WAKE_CHANNEL) as wake:
            while not shutdown.requested.is_set():
                await tick(worker_id)

                # For the healthcheck: proof the loop came back round.
                settings.worker_heartbeat_path.touch()

                # Woken by NOTIFY or by shutdown. The timeout only matters on a quiet
                # system, so scheduled work and dead workers still get noticed.
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
