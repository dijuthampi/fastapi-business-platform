"""Authentication schemas for request validation and response serialization."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    """Payload for multi-tenant registration (new organization + admin user)."""

    organization_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
        description="Name of the tenant organization",
        examples=["Acme Corp"],
    )
    organization_slug: str = Field(
        ...,
        min_length=2,
        max_length=255,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="Unique URL-friendly slug for the organization",
        examples=["acme-corp"],
    )
    email: EmailStr = Field(
        ...,
        description="Admin account email address",
        examples=["admin@acme.com"],
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Secure password for the admin account (minimum 8 characters)",
        examples=["StrongP@ssw0rd!"],
    )
    full_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Full name of the initial tenant administrator",
        examples=["Alice Johnson"],
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "organization_name": "Acme Corp",
                "organization_slug": "acme-corp",
                "email": "admin@acme.com",
                "password": "StrongPassword123!",
                "full_name": "Alice Johnson",
            }
        },
    )


class TokenResponse(BaseModel):
    """OAuth2 compliant JWT bearer token response."""

    access_token: str = Field(..., description="JWT bearer access token")
    token_type: str = Field(default="bearer", description="Token type")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
            }
        }
    )
