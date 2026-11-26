"""Automated unit and integration tests for Multi-Tenant Document Upload and ARQ Background Worker."""

from datetime import datetime, timezone
import io
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from uuid import UUID

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.document import Document
from app.models.organization import Organization
from app.models.user import User
from app.worker import extract_text_from_file, get_redis_pool, process_document_task

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_current_user() -> User:
    """Fixture providing a mock authenticated tenant User instance."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    org = Organization(
        id=org_id,
        name="Acme Enterprise",
        slug="acme-enterprise",
        is_active=True,
    )
    user = User(
        id=user_id,
        organization_id=org_id,
        email="operator@acme.com",
        role="member",
        is_active=True,
        organization=org,
    )
    return user


@pytest.fixture
def mock_redis_pool() -> AsyncMock:
    """Fixture providing a mock ARQ Redis pool."""
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock(return_value=None)
    return pool


# ---------------------------------------------------------------------------
# Integration Tests: Document Upload (POST /api/v1/documents/upload)
# ---------------------------------------------------------------------------

def test_upload_text_document_success(
    mock_current_user: User,
    mock_redis_pool: AsyncMock,
    tmp_path: Path,
) -> None:
    """Test successful document upload with valid auth, returning 202 and enqueuing job."""
    # Temporarily redirect upload directory to tmp_path
    original_upload_dir = settings.UPLOAD_DIR
    settings.UPLOAD_DIR = str(tmp_path / "uploads")

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_refresh(instance: Document):
        instance.created_at = datetime.now(timezone.utc)
        instance.updated_at = datetime.now(timezone.utc)

    mock_db.refresh = AsyncMock(side_effect=mock_refresh)

    async def override_get_user():
        return mock_current_user

    async def override_get_db():
        yield mock_db

    async def override_get_redis_pool():
        return mock_redis_pool

    app.dependency_overrides[get_current_user] = override_get_user
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_pool] = override_get_redis_pool

    try:
        file_content = b"FastAPI and ARQ background processing architecture report."
        file_name = "report.txt"
        files = {"file": (file_name, io.BytesIO(file_content), "text/plain")}

        token = create_access_token(
            subject=mock_current_user.id,
            organization_id=mock_current_user.organization_id,
            role="member",
        )
        headers = {"Authorization": f"Bearer {token}"}

        response = client.post(
            f"{settings.API_V1_STR}/documents/upload",
            files=files,
            headers=headers,
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data["filename"] == file_name
        assert data["content_type"] == "text/plain"
        assert data["file_size"] == len(file_content)
        assert data["status"] == "pending"
        assert data["organization_id"] == str(mock_current_user.organization_id)
        assert "id" in data

        # Verify job was enqueued in Redis pool
        mock_redis_pool.enqueue_job.assert_awaited_once_with(
            "process_document_task",
            data["id"],
        )
    finally:
        app.dependency_overrides.clear()
        settings.UPLOAD_DIR = original_upload_dir


def test_upload_document_rejects_unsupported_file_type(
    mock_current_user: User,
    mock_redis_pool: AsyncMock,
) -> None:
    """Test that uploading an unsupported executable (.exe) returns HTTP 400 Bad Request."""
    async def override_get_user():
        return mock_current_user

    async def override_get_redis_pool():
        return mock_redis_pool

    app.dependency_overrides[get_current_user] = override_get_user
    app.dependency_overrides[get_redis_pool] = override_get_redis_pool

    try:
        files = {"file": ("malicious.exe", io.BytesIO(b"MZ\x90\x00BinaryExe"), "application/x-msdownload")}
        response = client.post(
            f"{settings.API_V1_STR}/documents/upload",
            files=files,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert "Unsupported file type" in data["detail"]
    finally:
        app.dependency_overrides.clear()


def test_upload_document_rejects_oversized_file(
    mock_current_user: User,
    mock_redis_pool: AsyncMock,
    tmp_path: Path,
) -> None:
    """Test that uploading a file exceeding MAX_UPLOAD_SIZE_MB returns HTTP 400."""
    original_upload_dir = settings.UPLOAD_DIR
    original_max_size = settings.MAX_UPLOAD_SIZE_MB
    settings.UPLOAD_DIR = str(tmp_path / "uploads")
    # Temporarily set max size to 0 MB (rejection threshold)
    settings.MAX_UPLOAD_SIZE_MB = 0

    mock_db = AsyncMock()

    async def override_get_user():
        return mock_current_user

    async def override_get_db():
        yield mock_db

    async def override_get_redis_pool():
        return mock_redis_pool

    app.dependency_overrides[get_current_user] = override_get_user
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_pool] = override_get_redis_pool

    try:
        files = {"file": ("large_doc.txt", io.BytesIO(b"Chunk larger than limit"), "text/plain")}
        response = client.post(
            f"{settings.API_V1_STR}/documents/upload",
            files=files,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "exceeds maximum allowed size" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        settings.UPLOAD_DIR = original_upload_dir
        settings.MAX_UPLOAD_SIZE_MB = original_max_size


# ---------------------------------------------------------------------------
# Integration Tests: Multi-Tenant Isolation & Retrieval
# ---------------------------------------------------------------------------

def test_tenant_isolation_cross_organization_access_forbidden(
    mock_current_user: User,
) -> None:
    """Test that a user from Org A cannot retrieve a document belonging to Org B (returns 404)."""
    mock_db = AsyncMock()
    # Simulates query with Document.organization_id == Org A, which finds nothing for Org B's doc
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    async def override_get_user():
        return mock_current_user

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_current_user] = override_get_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        alien_doc_id = uuid.uuid4()
        response = client.get(f"{settings.API_V1_STR}/documents/{alien_doc_id}")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "Document not found"
    finally:
        app.dependency_overrides.clear()


def test_get_document_detail_success(mock_current_user: User) -> None:
    """Test successful retrieval of document details including extracted_text for matching tenant."""
    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        organization_id=mock_current_user.organization_id,
        uploaded_by_id=mock_current_user.id,
        filename="quarterly_report.pdf",
        file_path="/app/storage/uploads/test.pdf",
        content_type="application/pdf",
        file_size=10240,
        status="completed",
        extracted_text="Revenue grew 25% year-over-year in Q3.",
        error_message=None,
    )
    mock_doc.created_at = datetime.now(timezone.utc)
    mock_doc.updated_at = datetime.now(timezone.utc)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc
    mock_db.execute = AsyncMock(return_value=mock_result)

    async def override_get_user():
        return mock_current_user

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_current_user] = override_get_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        response = client.get(f"{settings.API_V1_STR}/documents/{doc_id}")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(doc_id)
        assert data["filename"] == "quarterly_report.pdf"
        assert data["status"] == "completed"
        assert data["extracted_text"] == "Revenue grew 25% year-over-year in Q3."
    finally:
        app.dependency_overrides.clear()


def test_list_documents_tenant_scoped(mock_current_user: User) -> None:
    """Test listing documents only returns records belonging to the user's organization."""
    doc1 = Document(
        id=uuid.uuid4(),
        organization_id=mock_current_user.organization_id,
        uploaded_by_id=mock_current_user.id,
        filename="doc1.txt",
        file_path="/path/1",
        content_type="text/plain",
        file_size=100,
        status="completed",
    )
    doc1.created_at = datetime.now(timezone.utc)
    doc1.updated_at = datetime.now(timezone.utc)

    doc2 = Document(
        id=uuid.uuid4(),
        organization_id=mock_current_user.organization_id,
        uploaded_by_id=mock_current_user.id,
        filename="doc2.pdf",
        file_path="/path/2",
        content_type="application/pdf",
        file_size=200,
        status="pending",
    )
    doc2.created_at = datetime.now(timezone.utc)
    doc2.updated_at = datetime.now(timezone.utc)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [doc1, doc2]
    mock_db.execute = AsyncMock(return_value=mock_result)

    async def override_get_user():
        return mock_current_user

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_current_user] = override_get_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        response = client.get(f"{settings.API_V1_STR}/documents")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 2
        assert data[0]["filename"] == "doc1.txt"
        assert data[1]["filename"] == "doc2.pdf"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Unit Tests: ARQ Background Worker Task & Text Extraction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_document_task_text_file_success(tmp_path: Path) -> None:
    """Unit test: process_document_task extracts text and transitions status to completed."""
    test_file = tmp_path / "sample.txt"
    sample_text = "Modern asynchronous Python backend with ARQ Redis worker."
    test_file.write_text(sample_text, encoding="utf-8")

    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        organization_id=uuid.uuid4(),
        filename="sample.txt",
        file_path=str(test_file),
        content_type="text/plain",
        file_size=len(sample_text),
        status="pending",
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    # Async context manager mock for AsyncSessionLocal()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aexit__.return_value = None

    with patch("app.worker.AsyncSessionLocal", mock_session_factory):
        await process_document_task({}, str(doc_id))

    assert mock_doc.status == "completed"
    assert mock_doc.extracted_text == sample_text
    assert mock_doc.error_message is None
    assert mock_session.commit.await_count >= 2


@pytest.mark.asyncio
async def test_process_document_task_pdf_extraction(tmp_path: Path) -> None:
    """Unit test: extract_text_from_file handles PDF cleanly."""
    pdf_path = str(tmp_path / "mock.pdf")
    # Create empty file
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF-1.4...")

    with patch("app.worker.pypdf.PdfReader") as mock_pdf_reader:
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1: Executive Summary"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page 2: Financial Results"
        mock_pdf_reader.return_value.pages = [mock_page1, mock_page2]

        extracted = extract_text_from_file(pdf_path, "application/pdf")
        assert "Page 1: Executive Summary" in extracted
        assert "Page 2: Financial Results" in extracted


@pytest.mark.asyncio
async def test_process_document_task_handles_missing_file_failure() -> None:
    """Unit test: worker gracefully marks status as 'failed' when file is missing without crashing."""
    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        organization_id=uuid.uuid4(),
        filename="nonexistent.txt",
        file_path="/tmp/definitely_not_here.txt",
        content_type="text/plain",
        file_size=100,
        status="pending",
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aexit__.return_value = None

    with patch("app.worker.AsyncSessionLocal", mock_session_factory):
        # Should not raise exception
        await process_document_task({}, str(doc_id))

    assert mock_doc.status == "failed"
    assert "File not found" in str(mock_doc.error_message)
    assert mock_session.rollback.awaited


# ---------------------------------------------------------------------------
# Integration Tests: Document Deletion (DELETE /api/v1/documents/{document_id})
# ---------------------------------------------------------------------------

def test_delete_document_success(mock_current_user: User, tmp_path: Path) -> None:
    """Test successful document deletion: removes disk file, deletes DB record, returns 204."""
    test_file = tmp_path / "delete_me.pdf"
    test_file.write_bytes(b"%PDF-1.4 sample content")
    assert test_file.exists()

    doc_id = uuid.uuid4()
    mock_document = Document(
        id=doc_id,
        organization_id=mock_current_user.organization_id,
        uploaded_by_id=mock_current_user.id,
        filename="delete_me.pdf",
        file_path=str(test_file),
        content_type="application/pdf",
        file_size=1024,
        status="completed",
    )

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_document
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.delete = AsyncMock()
    mock_db.commit = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: mock_current_user
    app.dependency_overrides[get_db] = lambda: mock_db

    try:
        response = client.delete(f"/api/v1/documents/{doc_id}")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.content == b""

        # Verify disk cleanup
        assert not test_file.exists()

        # Verify database record deletion
        mock_db.delete.assert_awaited_once_with(mock_document)
        mock_db.commit.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()


def test_delete_document_cross_tenant_isolation(
    mock_current_user: User,
    tmp_path: Path,
) -> None:
    """Test cross-tenant isolation: deleting another tenant's document returns 404 without leaking."""
    test_file = tmp_path / "other_tenant.pdf"
    test_file.write_bytes(b"%PDF-1.4 other tenant content")
    assert test_file.exists()

    other_doc_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_result = MagicMock()
    # Query with Document.organization_id == current_user.organization_id returns None
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.delete = AsyncMock()
    mock_db.commit = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: mock_current_user
    app.dependency_overrides[get_db] = lambda: mock_db

    try:
        response = client.delete(f"/api/v1/documents/{other_doc_id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "Document not found"

        # Verify file on disk is untouched
        assert test_file.exists()

        # Verify no database deletion occurred
        mock_db.delete.assert_not_called()
        mock_db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_delete_document_missing_disk_file_succeeds(mock_current_user: User) -> None:
    """Test that deleting a document whose physical file was already missing still cleans up DB."""
    doc_id = uuid.uuid4()
    mock_document = Document(
        id=doc_id,
        organization_id=mock_current_user.organization_id,
        uploaded_by_id=mock_current_user.id,
        filename="already_missing.pdf",
        file_path="/tmp/non_existent_path_404.pdf",
        content_type="application/pdf",
        file_size=1024,
        status="completed",
    )

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_document
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.delete = AsyncMock()
    mock_db.commit = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: mock_current_user
    app.dependency_overrides[get_db] = lambda: mock_db

    try:
        response = client.delete(f"/api/v1/documents/{doc_id}")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_db.delete.assert_awaited_once_with(mock_document)
        mock_db.commit.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()

