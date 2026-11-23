"""Document schemas for request validation, serialization, and API responses."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    """Document response schema for summary listing and upload confirmation."""

    id: UUID = Field(..., description="Unique document identifier (UUID)")
    organization_id: UUID = Field(..., description="Associated tenant organization identifier (UUID)")
    uploaded_by_id: Optional[UUID] = Field(default=None, description="User who uploaded the document (UUID)")
    filename: str = Field(..., description="Original filename of the uploaded document")
    content_type: str = Field(..., description="MIME content type (e.g. application/pdf, text/plain)")
    file_size: int = Field(..., description="File size in bytes")
    status: str = Field(..., description="Processing status: pending, processing, completed, failed")
    error_message: Optional[str] = Field(default=None, description="Error message if processing failed")
    created_at: datetime = Field(..., description="UTC creation timestamp")
    updated_at: datetime = Field(..., description="UTC last updated timestamp")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "c3eebc99-9c0b-4ef8-bb6d-6bb9bd380a33",
                "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
                "uploaded_by_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "filename": "quarterly-report.pdf",
                "content_type": "application/pdf",
                "file_size": 204800,
                "status": "pending",
                "error_message": None,
                "created_at": "2026-11-23T22:30:00Z",
                "updated_at": "2026-11-23T22:30:00Z",
            }
        },
    )


class DocumentDetailResponse(DocumentResponse):
    """Document detailed response including extracted text content."""

    extracted_text: Optional[str] = Field(default=None, description="Clean extracted plain text from document")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "c3eebc99-9c0b-4ef8-bb6d-6bb9bd380a33",
                "organization_id": "b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22",
                "uploaded_by_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "filename": "quarterly-report.pdf",
                "content_type": "application/pdf",
                "file_size": 204800,
                "status": "completed",
                "error_message": None,
                "extracted_text": "Executive Summary: Revenue grew 25% year-over-year...",
                "created_at": "2026-11-23T22:30:00Z",
                "updated_at": "2026-11-23T22:30:05Z",
            }
        },
    )
