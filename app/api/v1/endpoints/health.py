from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.schemas.health import HealthCheckResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check endpoint",
    description="Returns operational status, database connectivity, and environment metadata.",
)
async def get_health_status(db: AsyncSession = Depends(get_db)) -> HealthCheckResponse:
    """Check application health and verify database connectivity with a lightweight probe."""
    database_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        database_status = "disconnected"

    overall_status = "ok" if database_status == "connected" else "degraded"

    return HealthCheckResponse(
        status=overall_status,
        app_name=settings.PROJECT_NAME,
        developer="Diju",
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
        database=database_status,
    )
