"""AI service layer for text chunking, vector embedding generation, and grounded RAG answer synthesis."""

import asyncio
import base64
import hashlib
import logging
import math
import random
from typing import List, Optional

from fastapi import HTTPException, status
import httpx

from app.core.config import settings
from app.models.document_chunk import DocumentChunk

logger = logging.getLogger(__name__)


def chunk_text(
    text: str,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
) -> List[str]:
    """Split extracted document text cleanly into overlapping semantic chunks.

    Args:
        text: Raw document text to partition.
        chunk_size: Maximum character count per chunk (defaults to settings.CHUNK_SIZE).
        overlap: Character overlap between consecutive chunks (defaults to settings.CHUNK_OVERLAP).

    Returns:
        List of non-empty text chunks.
    """
    if not text or not text.strip():
        return []

    c_size = chunk_size if chunk_size is not None else settings.CHUNK_SIZE
    c_overlap = overlap if overlap is not None else settings.CHUNK_OVERLAP

    cleaned_text = text.strip()
    text_len = len(cleaned_text)

    if text_len <= c_size:
        return [cleaned_text]

    step = max(1, c_size - c_overlap)
    chunks: List[str] = []
    start = 0

    while start < text_len:
        end = min(start + c_size, text_len)
        chunk = cleaned_text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end == text_len:
            break
        start += step

    return chunks


async def extract_text_from_pdf_multimodal(file_path: str) -> str:
    """Extract full textual, tabular, and visual text from a PDF file using Gemini's native document understanding.

    Encodes the raw PDF bytes into Base64 and dispatches to Gemini generateContent with inline_data.
    Uses fallback model cascade (gemini-3.8-flash-lite -> gemini-3.8-flash -> gemini-3.5-flash)
    and retries on 429/503.

    Args:
        file_path: Path to the PDF file on disk.

    Returns:
        Extracted Markdown string representation of the PDF content.

    Raises:
        FileNotFoundError: If the file does not exist on disk.
        ValueError: If GEMINI_API_KEY is not configured.
        HTTPException: If all Gemini fallback models fail.
    """
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured for multimodal PDF extraction.")

    with open(file_path, "rb") as f:
        pdf_bytes = f.read()

    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

    fallback_models = ["gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "application/pdf",
                            "data": base64_pdf,
                        }
                    },
                    {
                        "text": (
                            "Extract all text, headings, tables, chart data, and text visible inside logos or images "
                            "from every page of this PDF document accurately in clean Markdown format. "
                            "Preserve all company names, client names, contact details, and numbers verbatim. "
                            "Do not summarize or omit anything."
                        )
                    },
                ],
            }
        ]
    }

    last_error_text = ""
    last_status_code = 502

    async with httpx.AsyncClient(timeout=90.0) as client:
        for model in fallback_models:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={settings.GEMINI_API_KEY}"
            )
            max_attempts = 3  # Initial attempt + up to 2 retries
            for attempt in range(max_attempts):
                try:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0] and "parts" in candidates[0]["content"]:
                        extracted_text = candidates[0]["content"]["parts"][0].get("text", "").strip()
                        if extracted_text:
                            logger.info(
                                f"Successfully extracted multimodal text via Gemini model '{model}' "
                                f"({len(extracted_text)} chars) for {file_path}"
                            )
                            return extracted_text
                    logger.warning(
                        f"Gemini model '{model}' returned empty candidate text for {file_path}. "
                        "Falling back to next model..."
                    )
                    break
                except httpx.HTTPStatusError as exc:
                    last_status_code = exc.response.status_code
                    last_error_text = exc.response.text

                    # Retry on temporary capacity spikes (429 Too Many Requests, 503 Service Unavailable)
                    if last_status_code in (429, 503) and attempt < max_attempts - 1:
                        logger.warning(
                            f"Gemini multimodal extraction with '{model}' returned HTTP {last_status_code} "
                            f"(attempt {attempt + 1}/{max_attempts}). Retrying in 1.5s..."
                        )
                        await asyncio.sleep(1.5)
                        continue

                    logger.warning(
                        f"Gemini model '{model}' failed with HTTP {last_status_code}: {last_error_text}. "
                        f"Falling back to next model..."
                    )
                    break
                except Exception as exc:
                    logger.warning(
                        f"Unexpected error in multimodal extraction with Gemini model '{model}': {exc}. "
                        f"Falling back to next model..."
                    )
                    last_error_text = str(exc)
                    break

    logger.error(
        f"All Gemini multimodal fallback models exhausted ({fallback_models}) for {file_path}. "
        f"Last error (HTTP {last_status_code}): {last_error_text}"
    )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Gemini multimodal extraction error across all fallback models: {last_error_text}",
    )


async def extract_text_from_pdf_multimodal(file_path: str) -> str:
    """Extract full textual, tabular, and visual text from a PDF file using Gemini's native document understanding.

    Encodes the raw PDF bytes into Base64 and dispatches to Gemini generateContent with inline_data.
    Uses fallback model cascade (gemini-3.8-flash-lite -> gemini-3.8-flash -> gemini-3.5-flash) and retries on 429/503.

    Args:
        file_path: Path to the PDF file on disk.

    Returns:
        Extracted Markdown string representation of the PDF content.

    Raises:
        ValueError: If GEMINI_API_KEY is not configured.
        FileNotFoundError: If the document file does not exist.
        HTTPException: If all Gemini fallback models fail.
    """
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured for multimodal PDF extraction.")

    with open(file_path, "rb") as f:
        pdf_bytes = f.read()

    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

    fallback_models = ["gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "application/pdf",
                            "data": base64_pdf,
                        }
                    },
                    {
                        "text": (
                            "Extract all text, headings, tables, chart data, and text visible inside logos or images "
                            "from every page of this PDF document accurately in clean Markdown format. "
                            "Preserve all company names, client names, contact details, and numbers verbatim. "
                            "Do not summarize or omit anything."
                        )
                    },
                ],
            }
        ]
    }

    last_error_text = ""
    last_status_code = 502

    async with httpx.AsyncClient(timeout=90.0) as client:
        for model in fallback_models:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={settings.GEMINI_API_KEY}"
            )
            max_attempts = 3  # Initial attempt + up to 2 retries
            for attempt in range(max_attempts):
                try:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0] and "parts" in candidates[0]["content"]:
                        extracted_text = candidates[0]["content"]["parts"][0].get("text", "").strip()
                        if extracted_text:
                            logger.info(
                                f"Successfully extracted multimodal text via Gemini model '{model}' "
                                f"({len(extracted_text)} chars) for {file_path}"
                            )
                            return extracted_text

                    logger.warning(
                        f"Gemini model '{model}' returned empty candidate text for {file_path}. "
                        "Falling back to next model..."
                    )
                    break
                except httpx.HTTPStatusError as exc:
                    last_status_code = exc.response.status_code
                    last_error_text = exc.response.text

                    # Retry on temporary capacity spikes (429 Too Many Requests, 503 Service Unavailable)
                    if last_status_code in (429, 503) and attempt < max_attempts - 1:
                        logger.warning(
                            f"Gemini multimodal extraction with '{model}' returned HTTP {last_status_code} "
                            f"(attempt {attempt + 1}/{max_attempts}). Retrying in 1.5s..."
                        )
                        await asyncio.sleep(1.5)
                        continue

                    logger.warning(
                        f"Gemini model '{model}' failed with HTTP {last_status_code}: {last_error_text}. "
                        f"Falling back to next model..."
                    )
                    break
                except Exception as exc:
                    logger.warning(
                        f"Unexpected error in multimodal extraction with Gemini model '{model}': {exc}. "
                        f"Falling back to next model..."
                    )
                    last_error_text = str(exc)
                    break

    logger.error(
        f"All Gemini multimodal fallback models exhausted ({fallback_models}) for {file_path}. "
        f"Last error (HTTP {last_status_code}): {last_error_text}"
    )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Gemini multimodal extraction error across all fallback models: {last_error_text}",
    )


def _generate_mock_embedding(text: str, dimension: int) -> List[float]:
    """Generate a deterministic, L2-normalized float vector seeded from text hash.

    Ensures consistent semantic similarity behavior in local dev, offline environments, and tests.
    """
    seed_int = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed_int)
    raw_vector = [rng.gauss(0.0, 1.0) for _ in range(dimension)]
    norm = math.sqrt(sum(x * x for x in raw_vector))
    if norm == 0:
        norm = 1.0
    return [round(x / norm, 8) for x in raw_vector]


async def generate_embedding(text: str) -> List[float]:
    """Generate a vector embedding for the given text using the configured AI provider.

    Supports 'mock' (deterministic fallback), 'openai' (text-embedding-3-small),
    and 'gemini' (gemini-embedding-001) providers.

    Args:
        text: Input text string to embed.

    Returns:
        List of floats of length settings.EMBEDDING_DIMENSION.
    """
    provider = settings.AI_PROVIDER.lower().strip()
    dimension = settings.EMBEDDING_DIMENSION

    if provider == "mock":
        return _generate_mock_embedding(text=text, dimension=dimension)

    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set. Falling back to mock embedding.")
            return _generate_mock_embedding(text=text, dimension=dimension)

        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "input": text,
            "model": "text-embedding-3-small",
            "dimensions": dimension,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"OpenAI embedding API error (HTTP {exc.response.status_code}): {exc.response.text}"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenAI embedding API error: {exc.response.text}",
            ) from exc

    elif provider == "gemini":
        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not set. Falling back to mock embedding.")
            return _generate_mock_embedding(text=text, dimension=dimension)

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-embedding-001:embedContent?key={settings.GEMINI_API_KEY}"
        )
        payload = {
            "content": {
                "parts": [{"text": text}]
            },
            "outputDimensionality": dimension,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["embedding"]["values"]
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"Gemini embedding API error (HTTP {exc.response.status_code}): {exc.response.text}"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Gemini embedding API error: {exc.response.text}",
            ) from exc

    else:
        logger.warning(f"Unknown AI_PROVIDER '{provider}'. Falling back to mock embedding.")
        return _generate_mock_embedding(text=text, dimension=dimension)


async def generate_rag_answer(question: str, context_chunks: List[DocumentChunk]) -> str:
    """Synthesize a context-grounded response for the given user question using retrieved chunks.

    Args:
        question: User query string.
        context_chunks: List of retrieved DocumentChunk entities belonging to the user's tenant.

    Returns:
        Synthesized answer grounded in the retrieved document context.
    """
    if not context_chunks:
        return (
            "I could not find any relevant information in your organization's uploaded "
            "documents to answer this question."
        )

    provider = settings.AI_PROVIDER.lower().strip()

    # Grounded context construction
    context_sections: List[str] = []
    for idx, chunk in enumerate(context_chunks, start=1):
        context_sections.append(
            f"[Source {idx} | Document: {chunk.document_id} | Chunk: {chunk.chunk_index}]\n"
            f"{chunk.content}"
        )
    formatted_context = "\n\n".join(context_sections)

    system_instruction = (
        "You are an AI enterprise assistant for a multi-tenant business platform. "
        "Answer the user's question strictly and truthfully based ONLY on the provided document context below. "
        "If the answer cannot be determined from the context, state that the provided documents do not contain "
        "sufficient information. Do not invent or hallucinate facts outside the context.\n\n"
        f"Context:\n{formatted_context}"
    )

    if provider == "mock":
        # Deterministic grounded synthesis for mock provider
        top_snippet = context_chunks[0].content[:250].strip()
        doc_count = len(set(c.document_id for c in context_chunks))
        return (
            f"Based on {len(context_chunks)} retrieved context chunk(s) across {doc_count} document(s) "
            f"in your organization: {top_snippet}..."
        )

    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY missing. Falling back to mock synthesis.")
            top_snippet = context_chunks[0].content[:250].strip()
            return f"[Dev Fallback] Based on your organization documents: {top_snippet}..."

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": question},
            ],
            "temperature": 0.2,
        }
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"OpenAI chat API error (HTTP {exc.response.status_code}): {exc.response.text}"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenAI chat API error: {exc.response.text}",
            ) from exc

    elif provider == "gemini":
        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY missing. Falling back to mock synthesis.")
            top_snippet = context_chunks[0].content[:250].strip()
            return f"[Dev Fallback] Based on your organization documents: {top_snippet}..."

        fallback_models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.8-flash-lite"]
        payload = {
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": question}]
                }
            ],
        }

        last_error_text = ""
        last_status_code = 502

        async with httpx.AsyncClient(timeout=45.0) as client:
            for model in fallback_models:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={settings.GEMINI_API_KEY}"
                )
                max_attempts = 3  # Initial attempt + up to 2 retries
                for attempt in range(max_attempts):
                    try:
                        resp = await client.post(url, json=payload)
                        resp.raise_for_status()
                        data = resp.json()
                        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    except httpx.HTTPStatusError as exc:
                        last_status_code = exc.response.status_code
                        last_error_text = exc.response.text

                        # Retry on temporary capacity spikes (429 Too Many Requests, 503 Service Unavailable)
                        if last_status_code in (429, 503) and attempt < max_attempts - 1:
                            logger.warning(
                                f"Gemini model '{model}' returned HTTP {last_status_code} "
                                f"(attempt {attempt + 1}/{max_attempts}). Retrying in 1.5s..."
                            )
                            await asyncio.sleep(1.5)
                            continue

                        logger.warning(
                            f"Gemini model '{model}' failed with HTTP {last_status_code}: {last_error_text}. "
                            f"Falling back to next model..."
                        )
                        break
                    except Exception as exc:
                        logger.warning(
                            f"Unexpected error with Gemini model '{model}': {exc}. "
                            f"Falling back to next model..."
                        )
                        last_error_text = str(exc)
                        break

        logger.error(
            f"All Gemini fallback models exhausted ({fallback_models}). "
            f"Last error (HTTP {last_status_code}): {last_error_text}"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini chat API error across all fallback models: {last_error_text}",
        )

    else:
        top_snippet = context_chunks[0].content[:250].strip()
        return f"[Fallback] Context found: {top_snippet}..."
