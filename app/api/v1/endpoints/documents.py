"""Document upload and status API endpoints with strict multi-tenant isolation."""

import logging
import os
from pathlib import Path
from typing import List
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.document import Document
from app.models.user import User
from app.schemas.document import DocumentDetailResponse, DocumentResponse
from app.worker import ArqRedis, get_redis_pool

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
}

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload document for async text extraction",
)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_pool: ArqRedis = Depends(get_redis_pool),
) -> Document:
    """Upload a document (PDF, TXT, or MD) for background processing.

    - Validates file type and size limit.
    - Saves file to tenant-isolated disk storage: `{UPLOAD_DIR}/{org_id}/{uuid}_{filename}`.
    - Creates a `Document` record scoped to current tenant (`status="pending"`).
    - Enqueues `process_document_task` in ARQ Redis worker queue.
    - Returns HTTP 202 Accepted with DocumentResponse metadata.
    """
    raw_filename = file.filename or "unnamed_document"
    original_filename = Path(raw_filename).name  # Prevent path traversal attacks
    extension = Path(original_filename).suffix.lower()

    # 1. Validate file extension and MIME type
    is_valid_ext = extension in ALLOWED_EXTENSIONS
    is_valid_mime = file.content_type in ALLOWED_MIME_TYPES

    if not is_valid_ext and not is_valid_mime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file type. Allowed formats: PDF (.pdf), Text (.txt), Markdown (.md). "
                f"Received MIME: '{file.content_type}', Extension: '{extension}'."
            ),
        )

    # Normalize content_type
    content_type = file.content_type or "text/plain"
    if extension == ".pdf":
        content_type = "application/pdf"
    elif extension == ".txt":
        content_type = "text/plain"
    elif extension == ".md":
        content_type = "text/markdown"

    # 2. Prepare tenant-isolated destination directory
    org_upload_dir = Path(settings.UPLOAD_DIR) / str(current_user.organization_id)
    org_upload_dir.mkdir(parents=True, exist_ok=True)

    doc_id = uuid.uuid4()
    stored_filename = f"{doc_id}_{original_filename}"
    file_path = org_upload_dir / stored_filename

    # 3. Stream file to disk while enforcing max size limit
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    bytes_read = 0
    chunk_size = 64 * 1024  # 64 KB chunks

    try:
        with open(file_path, "wb") as buffer:
            while chunk := await file.read(chunk_size):
                bytes_read += len(chunk)
                if bytes_read > max_bytes:
                    buffer.close()
                    if file_path.exists():
                        file_path.unlink()
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB}MB",
                    )
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        if file_path.exists():
            file_path.unlink()
        logger.exception(f"Failed to stream uploaded file to disk: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save uploaded file to disk",
        )

    # 4. Create Document record in database
    document = Document(
        id=doc_id,
        organization_id=current_user.organization_id,
        uploaded_by_id=current_user.id,
        filename=original_filename,
        file_path=str(file_path),
        content_type=content_type,
        file_size=bytes_read,
        status="pending",
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    # 5. Enqueue background text extraction task in ARQ Redis pool
    try:
        if redis_pool and hasattr(redis_pool, "enqueue_job"):
            await redis_pool.enqueue_job("process_document_task", str(document.id))
            logger.info(f"Enqueued document task for doc_id={document.id}")
    except Exception as exc:
        logger.error(f"Failed to enqueue task for document_id={document.id}: {exc}")

    return document


@router.get(
    "",
    response_model=List[DocumentResponse],
    summary="List all documents for current tenant",
)
async def list_documents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[Document]:
    """Retrieve all documents belonging strictly to the authenticated user's organization."""
    stmt = (
        select(Document)
        .where(Document.organization_id == current_user.organization_id)
        .order_by(Document.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="Get document details with extracted text",
)
async def get_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Retrieve document details and extracted text content.

    Enforces strict multi-tenant isolation:
    Returns 404 Not Found if the document does not exist or belongs to another organization.
    """
    stmt = select(Document).where(
        Document.id == document_id,
        Document.organization_id == current_user.organization_id,
    )
    result = await db.execute(stmt)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete document, disk storage, and cascaded vector chunks",
    description=(
        "Permanently deletes a document record and cascades to remove all associated "
        "vector chunks. Also removes the physical file from disk storage. "
        "Enforces strict multi-tenant isolation: returns 404 if the document does not "
        "exist or belongs to another organization."
    ),
)
async def delete_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete document with strict tenant isolation, safe disk cleanup, and cascaded chunk purging."""
    stmt = select(Document).where(
        Document.id == document_id,
        Document.organization_id == current_user.organization_id,
    )
    result = await db.execute(stmt)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Safely remove physical file from disk if present
    if document.file_path and os.path.exists(document.file_path):
        try:
            os.remove(document.file_path)
            logger.info(f"Deleted physical document file: {document.file_path}")
        except OSError as exc:
            logger.warning(f"Failed to remove file from disk {document.file_path}: {exc}")

    # Delete Document entity from database (cascades to document_chunks)
    await db.delete(document)
    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
