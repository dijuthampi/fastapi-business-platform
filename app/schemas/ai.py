"""Pydantic v2 schemas for RAG Question & Answering engine."""

from typing import List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    """Request payload for multi-tenant document RAG question answering."""

    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="The natural language question to ask against tenant documents.",
        examples=["What are our quarterly revenue projections?"],
    )
    top_k: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of top relevant context chunks to retrieve for grounding.",
        examples=[3],
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


class SourceCitation(BaseModel):
    """Source reference citation for a retrieved context chunk."""

    document_id: UUID = Field(..., description="UUID of the source Document.")
    filename: str = Field(..., description="Original filename of the source Document.")
    chunk_index: int = Field(..., description="Zero-based index of the chunk within the document.")
    snippet: str = Field(..., description="Representative excerpt of the chunk content.")
    similarity_score: float = Field(..., description="Cosine similarity score (0.0 to 1.0).")

    model_config = ConfigDict(from_attributes=True)


class AskResponse(BaseModel):
    """Grounded RAG answer response with source citations and provider attribution."""

    question: str = Field(..., description="The original user question.")
    answer: str = Field(..., description="Synthesized grounded answer.")
    sources: List[SourceCitation] = Field(
        default_factory=list,
        description="List of cited source chunks used to generate the answer.",
    )
    provider: str = Field(..., description="AI provider used for embedding and synthesis.")

    model_config = ConfigDict(from_attributes=True)
