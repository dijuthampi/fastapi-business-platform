"""Pydantic schemas for data transfer objects (DTOs) and API contracts."""

from app.schemas.ai import AskRequest, AskResponse, SourceCitation
from app.schemas.auth import RegisterRequest, TokenResponse
from app.schemas.document import DocumentDetailResponse, DocumentResponse
from app.schemas.health import HealthCheckResponse
from app.schemas.user import UserResponse

__all__ = [
    "AskRequest",
    "AskResponse",
    "DocumentDetailResponse",
    "DocumentResponse",
    "HealthCheckResponse",
    "RegisterRequest",
    "SourceCitation",
    "TokenResponse",
    "UserResponse",
]
