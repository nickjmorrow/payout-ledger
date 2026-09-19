"""Recipients: the people a programme sends money to."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Recipient


async def all_recipients(session: AsyncSession) -> list[Recipient]:
    result = await session.execute(select(Recipient).order_by(Recipient.full_name))
    return list(result.scalars())
