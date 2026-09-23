from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession
from app.api.schemas import ApiResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: DbSession) -> ApiResponse[dict[str, str]]:
    """Liveness, and a query to prove the database is reachable too."""
    await session.execute(text("select 1"))
    return ApiResponse(data={"status": "ok", "database": "ok"})
