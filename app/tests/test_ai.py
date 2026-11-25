"""Automated unit and integration tests for AI Text Chunking, Embeddings, and Multi-Tenant RAG Q&A Engine."""

import math
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from uuid import UUID

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.organization import Organization
from app.models.user import User
from app.services.ai_service import (
    chunk_text,
    generate_embedding,
    generate_rag_answer,
)
from app.worker import process_document_task

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_org_a() -> Organization:
    """Fixture providing Tenant Organization A."""
    return Organization(
        id=uuid.uuid4(),
        name="Alpha Corp",
        slug="alpha-corp",
        is_active=True,
    )


@pytest.fixture
def mock_org_b() -> Organization:
    """Fixture providing Tenant Organization B."""
    return Organization(
        id=uuid.uuid4(),
        name="Beta Corp",
        slug="beta-corp",
        is_active=True,
    )


@pytest.fixture
def mock_user_org_a(mock_org_a: Organization) -> User:
    """Fixture providing authenticated user in Tenant A."""
    return User(
        id=uuid.uuid4(),
        organization_id=mock_org_a.id,
        email="alice@alpha.com",
        role="member",
        is_active=True,
        organization=mock_org_a,
    )


@pytest.fixture
def mock_user_org_b(mock_org_b: Organization) -> User:
    """Fixture providing authenticated user in Tenant B."""
    return User(
        id=uuid.uuid4(),
        organization_id=mock_org_b.id,
        email="bob@beta.com",
        role="member",
        is_active=True,
        organization=mock_org_b,
    )


# ---------------------------------------------------------------------------
# 1. AI Service Unit Tests: Chunking & Deterministic Mock Embeddings
# ---------------------------------------------------------------------------

def test_chunk_text_empty_and_whitespace():
    """Verify chunk_text returns an empty list for empty or whitespace-only strings."""
    assert chunk_text("") == []
    assert chunk_text("   \n\t   ") == []


def test_chunk_text_single_chunk():
    """Verify text shorter than or equal to chunk_size is returned as a single chunk."""
    text = "Short text under 800 characters."
    chunks = chunk_text(text, chunk_size=800, overlap=150)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_overlapping_partitions():
    """Verify long text splits cleanly with specified overlap."""
    text = "A" * 1500
    chunks = chunk_text(text, chunk_size=800, overlap=200)
    # Step size is 800 - 200 = 600
    # Chunk 0: 0..800
    # Chunk 1: 600..1400
    # Chunk 2: 1200..1500
    assert len(chunks) == 3
    assert len(chunks[0]) == 800
    assert len(chunks[1]) == 800
    assert len(chunks[2]) == 300


@pytest.mark.anyio
async def test_generate_mock_embedding_dimensions_and_properties():
    """Verify mock embeddings generate exact dimension, unit length, and determinism."""
    text1 = "FastAPI and PostgreSQL pgvector enterprise architecture"
    text2 = "Different query for semantic chunking"

    vec1 = await generate_embedding(text1)
    vec1_repeat = await generate_embedding(text1)
    vec2 = await generate_embedding(text2)

    # 1. Dimension validation
    assert len(vec1) == settings.EMBEDDING_DIMENSION
    assert len(vec1) == 1536

    # 2. Determinism validation
    assert vec1 == vec1_repeat

    # 3. Text variation validation
    assert vec1 != vec2

    # 4. L2 Normalization validation (sum of squares ~ 1.0)
    norm = math.sqrt(sum(x * x for x in vec1))
    assert pytest.approx(norm, rel=1e-4) == 1.0


@pytest.mark.anyio
async def test_generate_rag_answer_empty_context():
    """Verify grounded RAG answer returns fallback message when no context exists."""
    answer = await generate_rag_answer("What is the strategy?", context_chunks=[])
    assert "could not find any relevant information" in answer.lower()


@pytest.mark.anyio
async def test_generate_rag_answer_with_mock_context(mock_org_a: Organization):
    """Verify grounded RAG answer synthesizes context in mock mode."""
    chunk = DocumentChunk(
        id=uuid.uuid4(),
        organization_id=mock_org_a.id,
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="Revenue grew by 45% year-over-year according to audited financials.",
        embedding=[0.0] * 1536,
    )
    answer = await generate_rag_answer("What was the revenue growth?", context_chunks=[chunk])
    assert "Revenue grew by 45%" in answer


# ---------------------------------------------------------------------------
# 2. Integration Tests: POST /api/v1/ai/ask & Multi-Tenant Isolation
# ---------------------------------------------------------------------------

def test_ask_endpoint_unauthenticated():
    """Verify 401 Unauthorized when no authentication token is provided."""
    response = client.post("/api/v1/ai/ask", json={"question": "What is our company budget?"})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_ask_endpoint_validation_error(mock_user_org_a: User):
    """Verify 422 Unprocessable Entity on invalid input (question too short or invalid top_k)."""
    async def override_get_user():
        return mock_user_org_a

    app.dependency_overrides[get_current_user] = override_get_user
    try:
        # Question too short (< 3 chars)
        res_short = client.post("/api/v1/ai/ask", json={"question": "hi"})
        assert res_short.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # top_k out of bounds (> 10)
        res_topk = client.post("/api/v1/ai/ask", json={"question": "Valid question?", "top_k": 50})
        assert res_topk.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    finally:
        app.dependency_overrides.clear()


def test_ask_endpoint_success_with_sources(mock_user_org_a: User, mock_org_a: Organization):
    """Verify 200 OK response with structured answers and citations for authenticated tenant."""
    doc_id = uuid.uuid4()
    mock_chunk = DocumentChunk(
        id=uuid.uuid4(),
        organization_id=mock_org_a.id,
        document_id=doc_id,
        chunk_index=0,
        content="Enterprise roadmap targets Q3 for multi-region vector deployment.",
        embedding=[0.0] * 1536,
    )

    # Mock DB query result row: (DocumentChunk, filename, distance)
    # Cosine distance 0.15 corresponds to similarity ~ 0.85
    mock_row = (mock_chunk, "q3_roadmap.pdf", 0.15)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]
    mock_db.execute = AsyncMock(return_value=mock_result)

    async def override_get_user():
        return mock_user_org_a

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_current_user] = override_get_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        response = client.post(
            "/api/v1/ai/ask",
            json={"question": "When is vector deployment targeted?", "top_k": 3},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert data["question"] == "When is vector deployment targeted?"
        assert "Enterprise roadmap targets Q3" in data["answer"]
        assert len(data["sources"]) == 1
        assert data["sources"][0]["document_id"] == str(doc_id)
        assert data["sources"][0]["filename"] == "q3_roadmap.pdf"
        assert data["sources"][0]["chunk_index"] == 0
        assert data["sources"][0]["similarity_score"] == 0.85
        assert "Enterprise roadmap" in data["sources"][0]["snippet"]
        assert data["provider"] == settings.AI_PROVIDER
    finally:
        app.dependency_overrides.clear()


def test_ask_endpoint_strict_tenant_isolation(
    mock_user_org_a: User,
    mock_user_org_b: User,
    mock_org_a: Organization,
    mock_org_b: Organization,
):
    """Verify strict tenant isolation: query strictly scopes DocumentChunk.organization_id."""
    executed_statements = []

    mock_db = AsyncMock()

    async def capturing_execute(stmt):
        executed_statements.append(stmt)
        mock_res = MagicMock()
        mock_res.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=capturing_execute)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    try:
        # 1. Query as User from Org A
        app.dependency_overrides[get_current_user] = lambda: mock_user_org_a
        res_a = client.post("/api/v1/ai/ask", json={"question": "Show Org A data", "top_k": 2})
        assert res_a.status_code == status.HTTP_200_OK

        # Verify SQL statement generated contains Org A's ID
        stmt_a = executed_statements[0]
        sql_text_a = str(stmt_a)
        assert "document_chunks.organization_id =" in sql_text_a

        # Check compiled params for Org A
        compiled_params_a = stmt_a.compile().params
        matching_org_a_params = [
            val for val in compiled_params_a.values() if val == mock_user_org_a.organization_id
        ]
        assert len(matching_org_a_params) == 1

        # 2. Query as User from Org B
        app.dependency_overrides[get_current_user] = lambda: mock_user_org_b
        res_b = client.post("/api/v1/ai/ask", json={"question": "Show Org B data", "top_k": 2})
        assert res_b.status_code == status.HTTP_200_OK

        stmt_b = executed_statements[1]
        compiled_params_b = stmt_b.compile().params
        matching_org_b_params = [
            val for val in compiled_params_b.values() if val == mock_user_org_b.organization_id
        ]
        assert len(matching_org_b_params) == 1

        # Verify Org A's query never included Org B's ID and vice-versa
        assert mock_user_org_b.organization_id not in compiled_params_a.values()
        assert mock_user_org_a.organization_id not in compiled_params_b.values()

    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 3. Worker Chunking & Vector Persistence Integration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_worker_process_document_task_chunks_and_embeddings(
    mock_org_a: Organization,
    tmp_path,
):
    """Verify process_document_task chunks extracted text and persists DocumentChunk rows."""
    doc_file = tmp_path / "financials.txt"
    doc_file.write_text("Quarterly financial report with important metrics. " * 30, encoding="utf-8")

    doc_id = uuid.uuid4()
    mock_document = Document(
        id=doc_id,
        organization_id=mock_org_a.id,
        filename="financials.txt",
        file_path=str(doc_file),
        content_type="text/plain",
        file_size=1200,
        status="pending",
    )

    added_chunks = []
    mock_session = AsyncMock()

    def mock_add(entity):
        if isinstance(entity, DocumentChunk):
            added_chunks.append(entity)

    mock_session.add = MagicMock(side_effect=mock_add)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.rollback = AsyncMock()

    mock_query_result = MagicMock()
    mock_query_result.scalar_one_or_none.return_value = mock_document
    mock_session.execute = AsyncMock(return_value=mock_query_result)

    # Patch AsyncSessionLocal to yield mock_session
    with patch("app.worker.AsyncSessionLocal") as mock_session_factory:
        mock_session_factory.return_value.__aenter__.return_value = mock_session
        mock_session_factory.return_value.__aexit__.return_value = None

        await process_document_task(ctx={}, document_id=str(doc_id))

    # Verify document completed
    assert mock_document.status == "completed"
    assert mock_document.error_message is None
    assert len(mock_document.extracted_text) > 0

    # Verify chunks were created and added
    assert len(added_chunks) > 0
    first_chunk = added_chunks[0]
    assert first_chunk.organization_id == mock_org_a.id
    assert first_chunk.document_id == doc_id
    assert first_chunk.chunk_index == 0
    assert len(first_chunk.embedding) == 1536
