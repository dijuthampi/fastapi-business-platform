"""Async background worker powered by ARQ and Redis for document processing."""

import logging
import os
from typing import Any
from uuid import UUID

import pypdf
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.document import Document

try:
    from arq.connections import ArqRedis, RedisSettings, create_pool
except ImportError:  # pragma: no cover
    ArqRedis = Any  # type: ignore[assignment, misc]
    RedisSettings = Any  # type: ignore[assignment, misc]
    create_pool = Any  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)


def get_redis_settings() -> RedisSettings:
    """Parse Redis connection settings from application configuration."""
    if hasattr(RedisSettings, "from_dsn"):
        return RedisSettings.from_dsn(settings.REDIS_URL)
    return None  # pragma: no cover


async def get_redis_pool() -> ArqRedis:
    """Create an ARQ Redis connection pool for dispatching background jobs.

    Can be used directly or injected as a FastAPI dependency.
    """
    redis_settings = get_redis_settings()
    return await create_pool(redis_settings)


def extract_text_from_file(file_path: str, content_type: str) -> str:
    """Extract clean text content from PDF or text/markdown document files.

    Args:
        file_path: Absolute or relative disk path to the document file.
        content_type: MIME type of the document.

    Returns:
        Extracted plain text content.

    Raises:
        FileNotFoundError: If the document file does not exist on disk.
        Exception: If PDF parsing fails.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found at path: {file_path}")

    is_pdf = content_type == "application/pdf" or file_path.lower().endswith(".pdf")

    if is_pdf:
        reader = pypdf.PdfReader(file_path)
        pages_text = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text.strip())
        return "\n\n".join(pages_text)
    else:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()


async def process_document_task(ctx: dict, document_id: str) -> None:
    """Background worker task to extract text from uploaded documents.

    Workflow:
      1. Opens an independent AsyncSessionLocal database session.
      2. Fetches Document by UUID and transitions status to 'processing'.
      3. Performs clean text extraction via pypdf (PDF) or UTF-8 read (.txt/.md).
      4. Saves extracted_text, transitions status to 'completed', and commits.
      5. On failure, captures error_message, transitions status to 'failed', and commits.
    """
    logger.info(f"Starting background processing for document_id={document_id}")

    try:
        doc_uuid = UUID(str(document_id))
    except (ValueError, TypeError, AttributeError) as exc:
        logger.error(f"Invalid UUID provided for document_id={document_id}: {exc}")
        return

    async with AsyncSessionLocal() as session:
        try:
            # 1. Fetch document and update status to processing
            stmt = select(Document).where(Document.id == doc_uuid)
            result = await session.execute(stmt)
            document = result.scalar_one_or_none()

            if not document:
                logger.error(f"Document with ID {document_id} not found in database")
                return

            document.status = "processing"
            document.error_message = None
            await session.commit()
            await session.refresh(document)

            # 2. Extract text from file on disk
            extracted_text = extract_text_from_file(
                file_path=document.file_path,
                content_type=document.content_type,
            )

            # 3. Mark completed and store extracted content
            document.extracted_text = extracted_text
            document.status = "completed"
            document.error_message = None
            await session.commit()
            logger.info(f"Successfully processed and completed document_id={document_id}")

        except Exception as exc:
            logger.exception(f"Error processing document {document_id}: {exc}")
            try:
                await session.rollback()
                # Re-query document to persist failed state cleanly
                stmt = select(Document).where(Document.id == doc_uuid)
                result = await session.execute(stmt)
                doc = result.scalar_one_or_none()
                if doc:
                    doc.status = "failed"
                    doc.error_message = str(exc)
                    await session.commit()
            except Exception as rollback_exc:
                logger.error(f"Failed to record failure status for document {document_id}: {rollback_exc}")


async def startup(ctx: dict) -> None:
    """Worker startup lifecycle hook."""
    logger.info("ARQ Worker service started. Ready to process background jobs.")


async def shutdown(ctx: dict) -> None:
    """Worker shutdown lifecycle hook."""
    logger.info("ARQ Worker service shutting down cleanly.")


class WorkerSettings:
    """ARQ Worker configuration settings."""

    functions = [process_document_task]
    redis_settings = get_redis_settings() if hasattr(RedisSettings, "from_dsn") else None
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    poll_delay = 0.5
