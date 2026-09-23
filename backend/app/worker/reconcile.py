"""The reconciliation pass, as a task that schedules its own successor.

Every scheduling goes through `task_service.schedule_once`, so there is one
chain however many workers run. See AGENTS.md > Recurring work runs once.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging import get_logger
from app.models import Task
from app.provider.registry import get_provider
from app.services import reconciliation_service, task_service
from app.worker.handlers import TaskOutcome, register

logger = get_logger(__name__)

RECONCILE = "reconcile"


@register(RECONCILE)
async def reconcile(session: AsyncSession, task: Task) -> TaskOutcome:
    report = await reconciliation_service.run(session, provider=get_provider())

    # Scheduled in the same transaction that records this pass's findings, so a
    # crash cannot end the chain. `successor_of` keeps this pass out of the count.
    await task_service.schedule_once(
        session,
        kind=RECONCILE,
        run_at=task_service.seconds_from_now(settings.reconcile_interval_seconds),
        request_id=task.request_id,
        successor_of=task.id,
    )
    await session.commit()

    if report.unresolved:
        logger.warning("reconciliation left unresolved findings", count=report.unresolved)
    return TaskOutcome("succeeded")


async def ensure_scheduled(session: AsyncSession) -> None:
    """Make sure a reconcile pass is queued or running. Called on every worker pass."""
    seeded = await task_service.schedule_once(
        session,
        kind=RECONCILE,
        run_at=task_service.seconds_from_now(settings.reconcile_interval_seconds),
    )
    # Commit either way, to release the advisory lock.
    await session.commit()
    if seeded is not None:
        logger.info("reconciliation seeded")
