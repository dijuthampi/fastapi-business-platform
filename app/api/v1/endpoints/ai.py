"""FastAPI endpoint router for AI & RAG Question & Answering operations."""

import logging
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.schemas.ai import AskRequest, AskResponse, SourceCitation
from app.services.ai_service import generate_embedding, generate_rag_answer

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/ask",
    response_model=AskResponse,
    status_code=status.HTTP_200_OK,
    summary="Query tenant documents with grounded RAG Q&A",
    description=(
        "Executes a cosine similarity vector search across all document chunks belonging "
        "strictly to the authenticated user's organization, retrieves the top_k relevant "
        "passages, and synthesizes a grounded answer with verifiable source citations."
    ),
)
async def ask_question(
    payload: AskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AskResponse:
    """Execute multi-tenant RAG search and synthesize a grounded answer."""
    # 1. Generate query embedding vector
    query_vector = await generate_embedding(payload.question)

    # 2. Compute cosine distance expression
    distance_expr = DocumentChunk.embedding.cosine_distance(query_vector).label("distance")

    # 3. Perform vector similarity query strictly isolated by tenant organization_id
    stmt = (
        select(DocumentChunk, Document.filename, distance_expr)
        .join(Document, DocumentChunk.document_id == Document.id)
        .where(DocumentChunk.organization_id == current_user.organization_id)
        .order_by(distance_expr.asc())
        .limit(payload.top_k)
    )
    result = await db.execute(stmt)
    records = result.all()

    # 4. Extract retrieved chunks and assemble structured source citations
    context_chunks: List[DocumentChunk] = []
    sources: List[SourceCitation] = []

    for chunk, filename, distance in records:
        context_chunks.append(chunk)

        # Distance is in [0, 2]; convert to similarity in [0, 1]
        dist_val = float(distance) if distance is not None else 1.0
        similarity = round(max(0.0, min(1.0, 1.0 - dist_val)), 4)
        snippet = chunk.content[:200] + ("..." if len(chunk.content) > 200 else "")

        sources.append(
            SourceCitation(
                document_id=chunk.document_id,
                filename=filename,
                chunk_index=chunk.chunk_index,
                snippet=snippet,
                similarity_score=similarity,
            )
        )

    # 5. Synthesize grounded answer via AI service
    answer = await generate_rag_answer(
        question=payload.question,
        context_chunks=context_chunks,
    )

    logger.info(
        f"RAG query processed for org_id={current_user.organization_id}: "
        f"found {len(sources)} sources, provider={settings.AI_PROVIDER}"
    )

    return AskResponse(
        question=payload.question,
        answer=answer,
        sources=sources,
        provider=settings.AI_PROVIDER,
    )
