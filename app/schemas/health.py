from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class HealthCheckResponse(BaseModel):
    """Pydantic v2 response schema for the API health check endpoint."""

    status: str = Field(..., description="Application operational status", examples=["ok"])
    app_name: str = Field(..., description="Application name")
    version: str = Field(..., description="Application version")
    environment: str = Field(..., description="Current running environment")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Current UTC timestamp of the health check",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "app_name": "FastAPI Business Platform",
                "version": "0.1.0",
                "environment": "development",
                "timestamp": "2026-11-18T22:00:00Z",
            }
        }
    )
