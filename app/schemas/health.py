from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class HealthCheckResponse(BaseModel):
    """Pydantic v2 response schema for the API health check endpoint."""

    status: str = Field(..., description="Application operational status", examples=["ok", "degraded"])
    app_name: str = Field(..., description="Application name")
    developer: str = Field(..., description="Developer name")
    version: str = Field(..., description="Application version")
    environment: str = Field(..., description="Current running environment")
    database: str = Field(..., description="Database connectivity status", examples=["connected", "disconnected"])
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Current UTC timestamp of the health check",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "app_name": "FastAPI Business Platform",
                "developer": "Diju",
                "version": "0.1.0",
                "environment": "development",
                "database": "connected",
                "timestamp": "2026-11-22T15:30:00Z",
            }
        }
    )
