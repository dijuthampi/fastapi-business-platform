"""User schemas for serialization and API responses."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserResponse(BaseModel):
    """User response schema for serialization (DTO)."""

    id: UUID = Field(..., description="Unique user identifier (UUID)")
    organization_id: UUID = Field(..., description="Associated organization identifier (UUID)")
    email: EmailStr = Field(..., description="User email address")
    full_name: Optional[str] = Field(default=None, description="User full name")
    role: str = Field(..., description="User role within the tenant organization")
    is_active: bool = Field(..., description="Active status indicator")
    created_at: datetime = Field(..., description="User registration UTC timestamp")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
                "email": "admin@acme.com",
                "full_name": "Alice Johnson",
                "role": "admin",
                "is_active": True,
                "created_at": "2026-11-22T15:30:00Z",
            }
        },
    )
