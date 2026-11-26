# FastAPI Business Platform: Architecture, Systems Blueprint & Technical Glossary

> **Target Audience:** Senior Backend Engineers, Applied AI Engineers, and Engineering Leaders with deep **PHP / Laravel 12** experience transitioning into **Python 3.12+ (FastAPI, SQLAlchemy 2.0 Async, ARQ, pgvector, and Retrieval-Augmented Generation)**.

---

## Table of Contents
1. [System Architecture & End-to-End Execution Flows](#1-system-architecture--end-to-end-execution-flows)
   - [High-Level 4-Container Topology](#high-level-4-container-topology)
   - [Sequence Flow 1: Multi-Tenant Registration & JWT Authentication](#sequence-flow-1-multi-tenant-registration--jwt-authentication)
   - [Sequence Flow 2: Async Document Ingestion, Chunking & Vector Indexing](#sequence-flow-2-async-document-ingestion-chunking--vector-indexing)
   - [Sequence Flow 3: Tenant-Isolated RAG Q&A Engine (Cosine Distance Search & Synthesis)](#sequence-flow-3-tenant-isolated-rag-qa-engine-cosine-distance-search--synthesis)
2. [Complete Project File-by-File Map](#2-complete-project-file-by-file-map)
3. [Deep-Dive Technical Glossary (Core Concepts & AI Terms)](#3-deep-dive-technical-glossary-core-concepts--ai-terms)
   - [Applied AI & RAG Concepts](#applied-ai--rag-concepts)
   - [FastAPI & Modern Python Backend Concepts](#fastapi--modern-python-backend-concepts)
4. [Master Cheat Sheet: Laravel 12 ↔ FastAPI Architecture Matrix](#4-master-cheat-sheet-laravel-12--fastapi-architecture-matrix)
5. [Essential Operations & Cheat Sheet Commands](#5-essential-operations--cheat-sheet-commands)

---

## 1. System Architecture & End-to-End Execution Flows

### High-Level 4-Container Topology

The `fastapi-business-platform` is engineered as a production-grade, asynchronous modular monolith deployed across four interconnected Docker services defined in `docker-compose.yml`:

```
                                  [ Client Browser / API Consumer ]
                                                  │
                                                  │ HTTP (Port 8000)
                                                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Docker Network: default                                                                        │
│                                                                                                │
│   ┌──────────────────────────────────┐                 ┌───────────────────────────────────┐   │
│   │   api (FastAPI ASGI Web Node)    │                 │   worker (ARQ Redis Job Node)     │   │
│   │                                  │                 │                                   │   │
│   │   • Uvicorn ASGI Server          │                 │   • ARQ Async Worker Loop         │   │
│   │   • JWT Auth & Multi-Tenancy     │                 │   • Multimodal PDF & pypdf        │   │
│   │   • Document Upload Streaming    │                 │   • Semantic Chunking (800/150)   │   │
│   │   • RAG Q&A Endpoint (/ai/ask)   │                 │   • Vector Embedding Ingestion    │   │
│   └─────────────────┬────────────────┘                 └─────────────────┬─────────────────┘   │
│                     │                                                    │                     │
│                     │ Enqueue Job ('process_document_task')              │ Pop Job             │
│                     ▼                                                    ▼                     │
│   ┌────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │                               redis:7-alpine (Redis Cache & Queue)                     │   │
│   │                               Container Port: 6379 (Host Port: 6379)                   │   │
│   └────────────────────────────────────────────────────────────────────────────────────────┘   │
│                     │                                                    │                     │
│                     │ Async SQL Queries (asyncpg)                        │ Persist Chunks      │
│                     ▼                                                    ▼                     │
│   ┌────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │                        db: pgvector/pgvector:pg16 (PostgreSQL 16)                      │   │
│   │             Container Port: 5432 (Host Mapped: 5433:5432) | Volume: postgres_data      │   │
│   │                                                                                        │   │
│   │   • Relational Tables: organizations, users, documents                                 │   │
│   │   • Vector Table: document_chunks (embedding: Vector(1536))                            │   │
│   │   • Vector Index: ix_document_chunks_embedding_hnsw (HNSW vector_cosine_ops)           │   │
│   └────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                │
│   Storage Mount: ./storage:/app/storage (Shared between api and worker containers)             │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Communication Pathways & Responsibilities:
1. **`api` Container (`uvicorn app.main:app` on port 8000):**
   - Receives all incoming HTTP requests via Uvicorn ASGI.
   - Enforces CORS policies, rate limiting, and dependency-injected JWT authentication (`Depends(get_current_user)`).
   - Validates payloads with Pydantic v2 DTOs.
   - Streams uploaded files directly to disk at `/app/storage/uploads/{org_id}/{uuid}_{filename}`.
   - Emits asynchronous background job commands to Redis via `arq.create_pool()`.
2. **`worker` Container (`arq app.worker.WorkerSettings`):**
   - Dedicated standalone process running the ARQ event loop.
   - Polls Redis for `process_document_task` jobs.
   - Reads the raw files from the shared `/app/storage` volume, extracts raw text using `pypdf` or UTF-8 decoding, chunks the text, computes 1536-dimensional embeddings, and writes `DocumentChunk` records directly to PostgreSQL.
3. **`db` Container (`pgvector/pgvector:pg16`):**
   - Native PostgreSQL 16 image bundled with the `pgvector` C-extension.
   - Exposes container port `5432`, mapped to host port `5433` to prevent collisions with any existing local PostgreSQL installations.
   - Stores relational business entities (`Organization`, `User`, `Document`) alongside mathematical embeddings (`DocumentChunk.embedding`).
4. **`redis` Container (`redis:7-alpine` on port 6379):**
   - In-memory data store functioning as the low-latency message broker and job queue for ARQ.

---

### Sequence Flow 1: Multi-Tenant Registration & JWT Authentication

This flow captures how a new tenant organization registers, generates an initial administrative user, authenticates, and obtains a signed JWT bearer token containing multi-tenant claims.

```
Client / Frontend              FastAPI Router (auth.py)        AuthService (auth_service.py)        PostgreSQL (db)
      │                                     │                                 │                             │
      │ 1. POST /api/v1/auth/register       │                                 │                             │
      │    {org_name, org_slug, email, pwd} │                                 │                             │
      ├────────────────────────────────────>│                                 │                             │
      │                                     │ 2. register_organization_and_admin(payload)                  │
      │                                     ├────────────────────────────────>│                             │
      │                                     │                                 │ 3. Check slug & email       │
      │                                     │                                 ├────────────────────────────>│
      │                                     │                                 │<────────────────────────────┤
      │                                     │                                 │ 4. Hash password (Argon2)   │
      │                                     │                                 │ 5. Atomic INSERT:           │
      │                                     │                                 │    - Organization (UUID)    │
      │                                     │                                 │    - User (role='admin')    │
      │                                     │                                 ├────────────────────────────>│
      │                                     │                                 │<────────────────────────────┤
      │                                     │<────────────────────────────────┤                             │
      │ 6. HTTP 201 Created (UserResponse)  │                                                               │
      │<────────────────────────────────────┤                                                               │
      │                                     │                                                               │
      │ 7. POST /api/v1/auth/login          │                                                               │
      │    (form_data: username, password)  │                                                               │
      ├────────────────────────────────────>│                                                               │
      │                                     │ 8. authenticate_user(email, password)                         │
      │                                     ├────────────────────────────────>│                             │
      │                                     │                                 │ 9. SELECT User by email     │
      │                                     │                                 ├────────────────────────────>│
      │                                     │                                 │<────────────────────────────┤
      │                                     │                                 │ 10. verify_password(Argon2) │
      │                                     │<────────────────────────────────┤                             │
      │                                     │                                                               │
      │                                     │ 11. create_access_token(sub=user.id, org_id=org.id, role)    │
      │ 12. HTTP 200 OK (TokenResponse)     │                                                               │
      │     {access_token: "ey...", bearer} │                                                               │
      │<────────────────────────────────────┤                                                               │
```

---

### Sequence Flow 2: Async Document Ingestion, Chunking & Vector Indexing

This flow illustrates the non-blocking ingestion architecture: uploads return immediately (`202 Accepted`), while CPU/IO-heavy extraction, text chunking, and vector embedding occur inside the background worker.

```
Client                       API Node (/documents/upload)         Redis Queue (ARQ)           Worker Node (worker.py)      PostgreSQL (pgvector)
  │                                       │                             │                              │                           │
  │ 1. POST /documents/upload             │                             │                              │                           │
  │    (multipart/form-data: PDF/TXT)     │                             │                              │                           │
  ├──────────────────────────────────────>│                             │                              │                           │
  │                                       │ 2. Stream to disk           │                              │                           │
  │                                       │    /storage/{org_id}/{file} │                              │                           │
  │                                       │ 3. INSERT Document(pending) │                              │                           │
  │                                       ├───────────────────────────────────────────────────────────────────────────────────────>│
  │                                       │ 4. enqueue_job('process_document_task', doc_id)            │                           │
  │                                       ├────────────────────────────>│                              │                           │
  │ 5. HTTP 202 Accepted (DocumentResponse)                             │                              │                           │
  │<──────────────────────────────────────┤                             │ 6. Pop job(doc_id)           │                           │
  │                                                                     ├─────────────────────────────>│                           │
  │                                                                                                    │ 7. UPDATE status='processing'
  │                                                                                                    ├──────────────────────────>│
  │                                                                                                    │ 8. Extract text: Gemini   │
  │                                                                                                    │    Multimodal Vision      │
  │                                                                                                    │    (auto pypdf fallback)  │
  │                                                                                                    │ 9. DELETE existing chunks │
  │                                                                                                    │    (Idempotency on retry) │
  │                                                                                                    ├──────────────────────────>│
  │                                                                                                    │ 10. chunk_text(800, 150)  │
  │                                                                                                    │ 11. generate_embedding()  │
  │                                                                                                    │     (Mock / OpenAI/Gemini)│
  │                                                                                                    │ 12. INSERT DocumentChunks │
  │                                                                                                    │     Vector(1536)          │
  │                                                                                                    │ 13. UPDATE status='completed'
  │                                                                                                    ├──────────────────────────>│
  │                                                                                                    │<──────────────────────────┤
```

---

### Sequence Flow 3: Tenant-Isolated RAG Q&A Engine (Cosine Distance Search & Synthesis)

This flow illustrates how user queries are converted to vector coordinates, matched against organization chunks using PostgreSQL's cosine operator `<=>`, and synthesized into a grounded, hallucination-resistant response.

```
Client (User)               API Node (/ai/ask)                 AIService (ai_service.py)       PostgreSQL (pgvector)      LLM (Mock/OpenAI/Gemini)
  │                                │                                      │                             │                             │
  │ 1. POST /api/v1/ai/ask         │                                      │                             │                             │
  │    {"question": "...", top_k:3}│                                      │                             │                             │
  ├───────────────────────────────>│                                      │                             │                             │
  │                                │ 2. generate_embedding(question)     │                             │                             │
  │                                ├─────────────────────────────────────>│                             │                             │
  │                                │<─────────────────────────────────────┤                             │                             │
  │                                │    Returns query_vector (1536 dims)  │                             │                             │
  │                                │                                      │                             │                             │
  │                                │ 3. Cosine Search Query:              │                             │                             │
  │                                │    SELECT chunks, doc.filename,      │                             │                             │
  │                                │           (embedding <=> query_vec) AS distance                    │                             │
  │                                │    WHERE organization_id = user.org_id (STRICT ISOLATION)          │                             │
  │                                │    ORDER BY distance ASC LIMIT top_k │                             │                             │
  │                                ├───────────────────────────────────────────────────────────────────>│                             │
  │                                │<───────────────────────────────────────────────────────────────────┤                             │
  │                                │    Returns top_k nearest chunks      │                             │                             │
  │                                │                                      │                             │                             │
  │                                │ 4. generate_rag_answer(question, context_chunks)                   │                             │
  │                                ├─────────────────────────────────────>│                                                           │
  │                                │                                      │ 5. Grounded System Prompt                                 │
  │                                │                                      │    "Answer strictly based ONLY on provided context..."     │
  │                                │                                      ├──────────────────────────────────────────────────────────>│
  │                                │                                      │<──────────────────────────────────────────────────────────┤
  │                                │                                      │    Returns synthesized answer                             │
  │                                │<─────────────────────────────────────┤                                                           │
  │                                │                                                                                                  │
  │ 6. HTTP 200 OK (AskResponse)   │                                                                                                  │
  │    - question: "..."           │                                                                                                  │
  │    - answer: "..."             │                                                                                                  │
  │    - sources: [ {doc_id, filename, chunk_idx, snippet, similarity_score: 0.94} ]                                                 │
  │    - provider: "mock"          │                                                                                                  │
  │<───────────────────────────────┤                                                                                                  │
```

---

## 2. Complete Project File-by-File Map

```
fastapi-business-platform/
├── alembic/                               # Database migrations root (Alembic)
│   ├── env.py                             # Async migration runtime & pgvector registration
│   ├── script.py.mako                     # Migration file template
│   └── versions/                          # Linear revision migration scripts
│       ├── 20261122_0001_create_organizations_and_users_tables.py
│       ├── 20261123_0002_create_documents_table.py
│       └── 20261124_0003_add_pgvector_and_document_chunks.py
├── alembic.ini                            # Alembic CLI configuration file
├── app/                                   # Application root package
│   ├── api/                               # Transport & routing layer
│   │   ├── deps.py                        # FastAPI dependency injection (Auth, DB, Redis)
│   │   └── v1/                            # API Version 1 endpoints & routing
│   │       ├── router.py                  # Master API v1 router mounting all sub-routers
│   │       └── endpoints/                 # Route controllers
│   │           ├── ai.py                  # POST /ai/ask (RAG Q&A Engine)
│   │           ├── auth.py                # POST /register, POST /login, GET /me
│   │           ├── documents.py           # POST /upload, GET /, GET /{document_id}
│   │           └── health.py              # GET /health (Liveness & DB ping)
│   ├── core/                              # Core kernel & low-level platform modules
│   │   ├── config.py                      # Pydantic BaseSettings singleton (.env parser)
│   │   ├── database.py                    # Async engine, sessionmaker, Base, TimestampMixin, get_db
│   │   ├── limiter.py                     # API rate limiting setup (slowapi singleton)
│   │   └── security.py                    # Argon2 password hashing & PyJWT token handling
│   ├── models/                            # SQLAlchemy 2.0 ORM database entities
│   │   ├── __init__.py                    # Model registry export (Base, Org, User, Doc, Chunk)
│   │   ├── document.py                    # Document entity (metadata & extracted text)
│   │   ├── document_chunk.py              # DocumentChunk entity with Vector(1536) embedding
│   │   ├── organization.py                # Organization entity (Tenant boundary)
│   │   └── user.py                        # User entity (Tenant member with role)
│   ├── schemas/                           # Pydantic v2 Data Transfer Objects (DTOs)
│   │   ├── __init__.py                    # Schema registry export
│   │   ├── ai.py                          # AskRequest, SourceCitation, AskResponse
│   │   ├── auth.py                        # RegisterRequest, TokenResponse
│   │   ├── document.py                    # DocumentResponse, DocumentDetailResponse
│   │   ├── health.py                      # HealthCheckResponse
│   │   └── user.py                        # UserResponse
│   ├── services/                          # Pure domain business logic layer
│   │   ├── ai_service.py                  # chunk_text, generate_embedding, generate_rag_answer
│   │   └── auth_service.py                # register_organization_and_admin, authenticate_user
│   ├── tests/                             # Automated testing suite (Pytest)
│   │   ├── conftest.py                    # Global test fixtures (forces AI_PROVIDER="mock")
│   │   ├── test_ai.py                     # Vector chunking, embedding, RAG & tenant isolation tests
│   │   ├── test_auth.py                   # Multi-tenant registration, login & JWT security tests
│   │   ├── test_documents.py              # Upload, storage & ARQ worker processing tests
│   │   └── test_health.py                 # Health probe & DB connectivity tests
│   ├── main.py                            # FastAPI application factory, middleware, lifespan
│   └── worker.py                          # ARQ background worker entrypoint & task definitions
├── docs/                                  # Architectural & project documentation
│   └── ARCHITECTURE_AND_GLOSSARY.md       # Complete architectural reference & glossary
├── storage/                               # Host volume mounted for uploads (/app/storage)
│   └── uploads/                           # Organized by tenant ID: {org_id}/{doc_id}_{filename}
├── .env.example                           # Example environment template
├── .env                                   # Active environment configuration
├── docker-compose.yml                     # 4-container production topology (api, worker, db, redis)
├── Dockerfile                             # Multi-stage production container build (Python 3.12-slim)
└── requirements.txt                       # Locked production dependencies
```

### Detailed Component Inventory

| File Path | Architectural Layer | Purpose & Key Classes / Functions |
| :--- | :--- | :--- |
| `app/main.py` | Presentation / Bootstrap | `create_application()`, `lifespan()` context manager, CORS middleware setup, router mounting. |
| `app/core/config.py` | Configuration | `Settings(BaseSettings)` class reading `.env`; exports cached `settings` singleton. |
| `app/core/database.py` | Persistence Engine | `async_engine`, `AsyncSessionLocal`, `Base(DeclarativeBase)`, `TimestampMixin`, `get_db()`. |
| `app/core/limiter.py` | Rate Limiting | Rate limiter singleton with remote-address keying (`slowapi`). |
| `app/core/security.py` | Security Kernel | `hash_password()`, `verify_password()`, `create_access_token()`, `decode_access_token()`. |
| `app/api/deps.py` | Inversion of Control | `oauth2_scheme`, `get_current_user()`, `get_current_active_admin()`. |
| `app/api/v1/router.py` | Transport Routing | Aggregates sub-routers (`health`, `auth`, `documents`, `ai`) under `/api/v1`. |
| `app/api/v1/endpoints/documents.py` | Transport Controller | `POST /upload`, `GET /`, `GET /{document_id}`, `DELETE /{document_id}`. |
| `app/api/v1/endpoints/ai.py` | Transport Controller | `POST /api/v1/ai/ask` RAG Q&A endpoint executing vector queries. |
| `app/models/document_chunk.py` | ORM Entity | `DocumentChunk`: `id`, `organization_id`, `document_id`, `chunk_index`, `content`, `embedding: Vector(1536)`. |
| `app/services/ai_service.py` | Domain Service | `chunk_text()`, `extract_text_from_pdf_multimodal()`, `generate_embedding()`, `generate_rag_answer()`, `_generate_mock_embedding()`. |
| `app/worker.py` | Background Engine | `process_document_task()`, `extract_text_from_file()`, `WorkerSettings` for ARQ runner. |
| `alembic/versions/20261124_0003...` | Database Migration | Runs `CREATE EXTENSION IF NOT EXISTS vector`, creates `document_chunks` table and HNSW cosine index. |

---

## 3. Deep-Dive Technical Glossary (Core Concepts & AI Terms)

### Applied AI & RAG Concepts

#### 1. RAG (Retrieval-Augmented Generation) & Grounding
- **What it is:** An architectural technique where an LLM is not expected to know all private business facts from its pre-training weights. Instead, relevant proprietary facts are dynamically fetched from an external database at query time and injected into the prompt context for the LLM to read before answering.
- **Why we used it:** Without RAG, LLMs hallucinate plausible-sounding falsehoods and have zero awareness of an organization's private PDFs, markdown notes, or financial spreadsheets. RAG "grounds" the model strictly in verified tenant documents.
- **How it works in our code:** In [app/api/v1/endpoints/ai.py](file:///c:/laragon/www/fastapi-business-platform/app/api/v1/endpoints/ai.py), the incoming user question retrieves the top 3 closest chunks belonging to that specific tenant. In [app/services/ai_service.py](file:///c:/laragon/www/fastapi-business-platform/app/services/ai_service.py#L112-L148), `generate_rag_answer` formats these chunks into an explicit system prompt: *"Answer strictly based ONLY on the provided document context below. If the answer cannot be determined, state that the documents do not contain sufficient information."*
- **Laravel 12 Mental Mapping:** In Laravel, this would be an `AskQuestionAction` service class that uses Laravel Scout to query relevant Eloquent models, constructs a prompt string with the chunk excerpts, and dispatches the payload to the OpenAI PHP SDK (`OpenAI::chat()->create(...)`).

#### 2. Vector Embeddings (1536 Dimensions)
- **What it is:** A numerical representation of semantic meaning. An embedding model converts an arbitrary string of text into an array (vector) of floating-point numbers. Words and sentences with similar conceptual meaning are positioned close together in high-dimensional geometric space.
- **Why we used it:** Keyword/regex search fails when synonyms are used (e.g., searching for "revenue" wouldn't find "sales turnover" or "fiscal earnings"). Vector embeddings capture conceptual proximity. We chose `1536` dimensions because it is the native output dimension of OpenAI's `text-embedding-3-small` and Gemini's `gemini-embedding-001` (configured via `outputDimensionality=1536`).
- **How it works in our code:** In [app/services/ai_service.py](file:///c:/laragon/www/fastapi-business-platform/app/services/ai_service.py#L73-L122), `generate_embedding(text)` calls OpenAI/Gemini REST APIs using `httpx.AsyncClient`. When `AI_PROVIDER="mock"`, `_generate_mock_embedding` generates a deterministic 1536-dimensional float vector seeded by SHA-256 and normalized using Euclidean L2-norm so local development runs without external API keys.
- **Laravel 12 Mental Mapping:** Think of an embedding as a casted JSON column `array` in Eloquent, generated in a model observer or queued job using an AI service class.

#### 3. Semantic Chunking & Overlap (`CHUNK_SIZE=800`, `CHUNK_OVERLAP=150`)
- **What it is:** Partitioning large text into smaller, digestible windows with deliberate overlap. 
- **Why we used it:**
  1. *Context Window Efficiency:* Embedding a 50-page PDF as a single vector washes out specific details into a vague semantic average.
  2. *Granular Retrieval:* Small chunks allow the database to return only the exact paragraph that answers the user's question.
  3. *Sentence Boundary Protection:* The `150` character overlap ensures that facts split right at the 800-character boundary are not cut in half, preserving semantic context across adjacent chunks.
- **How it works in our code:** In [app/services/ai_service.py](file:///c:/laragon/www/fastapi-business-platform/app/services/ai_service.py#L17-L55), `chunk_text(text, chunk_size=800, overlap=150)` uses a sliding window with `step = chunk_size - overlap` (advancing by 650 characters each iteration).
- **Laravel 12 Mental Mapping:** Analogous to a custom Laravel Collection macro or string helper: `Str::of($text)->chunkWithOverlap(800, 150)`.

#### 4. Vector Indexing & `pgvector`
- **What it is:** An open-source PostgreSQL extension that introduces a native `vector` data type and specialized indexing algorithms (HNSW and IVFFlat) directly inside PostgreSQL.
- **Why we used it:** Avoids introducing separate vector databases (like Pinecone, Milvus, or Qdrant). Storing relational data (`organizations`, `documents`) and vectors (`document_chunks`) inside the same PostgreSQL engine enables atomic transactions, cascade deletes, and single-query joins with strict tenant filtering.
- **How it works in our code:** 
  - `docker-compose.yml` uses the `pgvector/pgvector:pg16` Docker image.
  - Migration `20261124_0003_add_pgvector_and_document_chunks.py` runs `CREATE EXTENSION IF NOT EXISTS vector` and builds an `HNSW` index on `document_chunks.embedding` using `vector_cosine_ops`.
  - [app/models/document_chunk.py](file:///c:/laragon/www/fastapi-business-platform/app/models/document_chunk.py) maps `embedding: Mapped[list[float]] = mapped_column(Vector(1536))`.
- **Laravel 12 Mental Mapping:** Equivalent to running a migration with `$table->addColumn('vector', 'embedding', ['dimensions' => 1536])` using raw SQL statements (`DB::statement('CREATE EXTENSION IF NOT EXISTS vector')`).

#### 5. Cosine Distance (`<=>`) vs. Cosine Similarity
- **What it is:** The mathematical metric used to determine how close two vectors point in high-dimensional space regardless of magnitude:
  $$\text{Cosine Similarity} = \cos(\theta) = \frac{A \cdot B}{\|A\| \|B\|}$$
  $$\text{Cosine Distance} = 1 - \cos(\theta)$$
- **Why we used it:** In `pgvector`, the `<=>` operator computes **Cosine Distance**. A distance of `0.0` represents identical vectors pointing in the exact same direction. A distance of `1.0` indicates orthogonality (completely unrelated).
- **How it works in our code:** In [app/api/v1/endpoints/ai.py](file:///c:/laragon/www/fastapi-business-platform/app/api/v1/endpoints/ai.py#L42-L55):
  ```python
  distance_expr = DocumentChunk.embedding.cosine_distance(query_vector).label("distance")
  stmt = select(DocumentChunk, Document.filename, distance_expr)...order_by(distance_expr.asc())
  ```
  The endpoint transforms distance back to human-readable similarity: `similarity_score = round(max(0.0, min(1.0, 1.0 - distance)), 4)`.
- **Laravel 12 Mental Mapping:** In Eloquent, this corresponds to an `orderByRaw('embedding <=> ? ASC', [$vectorString])` query clause.

#### 6. Multimodal PDF Extraction (Gemini Native Document Understanding vs. pypdf Fallback)
- **What it is:** Passing raw PDF bytes (base64-encoded) directly into Gemini's native multimodal vision pipeline (`gemini-3.8-flash-lite` -> `gemini-3.8-flash` -> `gemini-3.5-flash`), prompting the model to extract all visible text, headings, tabular data, and words printed inside graphics, logos, and charts into structured Markdown.
- **Why we used it:**
  1. *Visual & Tabular Text:* Traditional PDF text streams (`pypdf`) only read digital character sequences. Scanned pages, flattened tables, and text embedded in client logos or infographics are completely invisible to `pypdf`.
  2. *Zero OS-Level Bloat:* Traditional OCR requires system-level C/C++ packages (`tesseract-ocr`, `libtesseract`, `poppler-utils`, `opencv`), bloating Docker images by 500MB+ and draining CPU cycles. Gemini Native Vision processes documents visually via cloud API with zero container bloat.
  3. *High-Throughput Graceful Degradation:* The pipeline attempts multimodal extraction with retry logic for `503`/`429` errors. If `AI_PROVIDER != "gemini"` or if the Gemini API call fails, the system seamlessly falls back to local `pypdf` extraction, ensuring 100% offline and testing compatibility.
- **How it works in our code:** [app/services/ai_service.py](file:///c:/laragon/www/fastapi-business-platform/app/services/ai_service.py) implements `extract_text_from_pdf_multimodal(file_path)` which reads the PDF file, converts it to base64, dispatches to Gemini's `inline_data` endpoint, and handles retries. [app/worker.py](file:///c:/laragon/www/fastapi-business-platform/app/worker.py) calls this function whenever `document.content_type == "application/pdf"`, automatically falling back to `extract_text_from_file(file_path, content_type)` on failure.
- **Laravel 12 Mental Mapping:** In Laravel, this mirrors a queued `ExtractDocumentJob` that attempts visual extraction via Google Gemini or AWS Textract SDK (`Http::timeout(90)->post(...)`) before falling back to a local PHP PDF package like `Spatie\PdfToText\Pdf`.

---

### FastAPI & Modern Python Backend Concepts

#### 6. ASGI & `async`/`await` Concurrency (Uvicorn vs. PHP-FPM)
- **What it is:** ASGI (Asynchronous Server Gateway Interface) is the asynchronous successor to WSGI. It allows a single Python process to handle thousands of concurrent requests over an event loop without blocking on I/O.
- **Why we used it:** In traditional synchronous web servers (like PHP-FPM or WSGI Gunicorn), every waiting request (such as an external API call to OpenAI or a slow database query) blocks an entire OS thread or process worker. Under ASGI with `async`/`await`, when a route handler awaits an I/O operation (`await db.execute(...)` or `await client.post(...)`), Uvicorn pauses that specific coroutine and immediately switches CPU execution to serve other incoming requests.
- **How it works in our code:** All endpoints (`async def ask_question(...)`), database sessions (`AsyncSession`), and HTTP requests (`httpx.AsyncClient`) are non-blocking coroutines.
- **Laravel 12 Mental Mapping:** PHP-FPM spawns dedicated child processes per concurrent request. FastAPI under Uvicorn is architecturally equivalent to **Laravel Octane** running on top of **Swoole** or **RoadRunner**, where long-lived worker loops handle requests asynchronously in memory without rebooting the framework bootstrap on every request.

#### 7. Pydantic v2 Schemas (DTOs & Validation)
- **What it is:** Data validation and parsing library built on a high-performance Rust core (`pydantic-core`).
- **Why we used it:** Provides strict type safety, automatic request payload validation, and deterministic JSON response serialization with OpenAPI schema generation.
- **How it works in our code:**
  - `AskRequest` validates minimum question length and clamps `top_k` between 1 and 10.
  - `from_attributes=True` (formerly `orm_mode=True` in v1) instructs Pydantic to read attributes directly off SQLAlchemy ORM models (e.g., `user.id`, `user.email`) rather than requiring raw dictionaries.
- **Laravel 12 Mental Mapping:** Pydantic requests map directly to **Laravel Form Requests** (`rules()`), while Pydantic response models map directly to **Laravel API Resources** (`JsonResource`) or Spatie's `Laravel-Data` DTOs.

#### 8. SQLAlchemy 2.0 Async ORM (`AsyncSession`, `select()`)
- **What it is:** Python's leading object-relational mapper, completely overhauled in version 2.0 to adopt a clean, explicit, SQL-first query syntax paired with asynchronous drivers (`asyncpg`).
- **Why we used it:** Prevents hidden "N+1" query explosions. In SQLAlchemy 2.0, lazy-loading relationships synchronously across async boundaries throws an explicit error (`MissingGreenlet`), forcing engineers to explicitly declare join strategies (`lazy="selectin"` or `lazy="joined"`).
- **How it works in our code:** 
  - [app/core/database.py](file:///c:/laragon/www/fastapi-business-platform/app/core/database.py) configures `create_async_engine` and `async_sessionmaker`.
  - Queries use modern 2.0 executable select statements: `stmt = select(Document).where(...); res = await db.execute(stmt); res.scalars().all()`.
- **Laravel 12 Mental Mapping:** Corresponds to **Laravel Eloquent ORM**. `select(User).where(...)` corresponds to `User::query()->where(...)`. The explicit `lazy="selectin"` relationship mapping corresponds to Eloquent's eager loading `$with = ['chunks']`.

#### 9. Alembic Migrations
- **What it is:** The standard database schema migration tool for SQLAlchemy.
- **Why we used it:** Maintains a version-controlled, bidirectional (`upgrade()` and `downgrade()`) audit log of every change to database structure.
- **How it works in our code:** [alembic/env.py](file:///c:/laragon/www/fastapi-business-platform/alembic/env.py) connects asynchronously to PostgreSQL using `settings.DATABASE_URL` and inspects `Base.metadata` to detect schema differences.
- **Laravel 12 Mental Mapping:** Exactly identical to **Laravel Database Migrations** (`database/migrations/*.php`) managed via `php artisan migrate` and `php artisan migrate:rollback`.

#### 10. Dependency Injection (`Depends`)
- **What it is:** FastAPI's built-in, type-safe Inversion of Control (IoC) system for declaring reusable dependencies on routes.
- **Why we used it:** Solves cross-cutting concerns (database sessions, authentication, role verification) cleanly at the route level while making unit testing trivial via `app.dependency_overrides`.
- **How it works in our code:** In [app/api/deps.py](file:///c:/laragon/www/fastapi-business-platform/app/api/deps.py):
  - `get_db` yields an active `AsyncSession` that automatically commits or rolls back and closes at the end of the request.
  - `get_current_user` depends on `oauth2_scheme` and `get_db` to extract and verify the JWT token and load the User.
  - `get_current_active_admin` depends on `get_current_user` to assert `user.role == "admin"`.
- **Laravel 12 Mental Mapping:** Combines the power of **Laravel Route Middleware** (`auth:sanctum`), **Form Request Authorization**, and **Laravel Service Container Injection** (`app()->make(...)`).

#### 11. JWT Stateless Authentication
- **What it is:** Signed tokens containing encoded claims (`sub`, `org_id`, `role`, `exp`) verified cryptographically using HMAC-SHA256 (`HS256`).
- **Why we used it:** Eliminates database session lookups on every request, allowing the API tier to scale horizontally without shared session caches.
- **How it works in our code:** [app/core/security.py](file:///c:/laragon/www/fastapi-business-platform/app/core/security.py) signs tokens via `create_access_token` using `settings.SECRET_KEY`. [app/api/deps.py](file:///c:/laragon/www/fastapi-business-platform/app/api/deps.py) extracts the Bearer token and verifies expiration and signature integrity.
- **Laravel 12 Mental Mapping:** Maps directly to **Laravel Sanctum** (token authentication) or the `tymon/jwt-auth` package.

#### 12. Redis & ARQ Background Workers
- **What it is:** An asynchronous job queue library tailored for `asyncio` and backed by Redis.
- **Why we used it:** Long-running I/O (extracting 200 pages of PDF text, making 50 external HTTP calls to embedding APIs) will timeout HTTP gateways if executed during a web request. ARQ decouples ingestion from the web response.
- **How it works in our code:**
  - `upload_document` calls `await redis_pool.enqueue_job("process_document_task", str(document.id))` and returns `202 Accepted`.
  - `app/worker.py` runs a dedicated `WorkerSettings` loop that consumes the queue, executing `process_document_task` with isolated database sessions.
- **Laravel 12 Mental Mapping:** Exactly identical to **Laravel Queues & Horizon** (`dispatch(new ProcessDocumentJob($docId))->onQueue('default')`).

#### 13. Multi-Tenant Data Isolation
- **What it is:** Logical partitioning of all relational and vector data by a tenant identifier (`organization_id`).
- **Why we used it:** In enterprise B2B SaaS, Tenant A must never view, search, or synthesize answers from Tenant B's confidential documents.
- **How it works in our code:**
  - Every entity table (`users`, `documents`, `document_chunks`) has an indexed foreign key column `organization_id`.
  - Every SELECT query in route endpoints and vector searches explicitly attaches `WHERE organization_id = current_user.organization_id`.
  - Verified by automated tests in [app/tests/test_ai.py](file:///c:/laragon/www/fastapi-business-platform/app/tests/test_ai.py) asserting that queries generated for Org A never query Org B's data.
- **Laravel 12 Mental Mapping:** Corresponds to an **Eloquent Global Scope** or a `BelongsToTenant` trait (`static::addGlobalScope(new TenantScope)`).

#### 14. Swagger UI / OpenAPI (`/docs`)
- **What it is:** Interactive, browser-based documentation automatically generated from route type annotations and Pydantic schemas.
- **Why we used it:** Zero-maintenance, live API documentation accessible at `http://localhost:8000/docs` in development mode.
- **Laravel 12 Mental Mapping:** Equivalent to packages like `dedoc/scramble` or `l5-swagger` (Swagger-PHP annotations).

#### 15. API Rate Limiting (`slowapi`) & Anti-Abuse Protection
- **What it is:** A rate-limiting framework for FastAPI based on `limits` and `slowapi`. It throttles excessive requests per IP address or user identity to protect against brute-force attacks and denial-of-service spikes.
- **Why we used it:** Without rate limiting, authentication endpoints (`POST /auth/login`, `POST /auth/register`) can be brute-forced, and expensive LLM endpoints (`POST /ai/ask`) can quickly exhaust cloud API quotas or spike infrastructure costs. We enforce `10/minute` on auth and `20/minute` on RAG queries, returning standard HTTP `429 Too Many Requests`.
- **How it works in our code:** [app/core/limiter.py](file:///c:/laragon/www/fastapi-business-platform/app/core/limiter.py) initializes `Limiter(key_func=get_remote_address)`. In [app/main.py](file:///c:/laragon/www/fastapi-business-platform/app/main.py), it is attached to `app.state.limiter` with `_rate_limit_exceeded_handler`. Individual endpoints use `@limiter.limit(...)`. In [app/tests/conftest.py](file:///c:/laragon/www/fastapi-business-platform/app/tests/conftest.py), `limiter.enabled = False` disables rate limits during test execution.
- **Laravel 12 Mental Mapping:** Maps directly to **Laravel Route Rate Limiting** (`RateLimiter::for('api', ...)` and `throttle:10,1` middleware).

#### 16. Multi-Tenant Document Deletion & Cascaded Purging
- **What it is:** The complete and secure destruction of an uploaded document, including its metadata, physical file on disk, and all associated vector embeddings.
- **Why we used it:** Tenants need full data lifecycle governance (e.g. GDPR compliance / Right to be Forgotten). Deletion must be atomic and strictly isolated so Tenant A can never delete or infer the existence of Tenant B's files.
- **How it works in our code:** In [app/api/v1/endpoints/documents.py](file:///c:/laragon/www/fastapi-business-platform/app/api/v1/endpoints/documents.py), `DELETE /documents/{document_id}` filters strictly by `Document.organization_id == current_user.organization_id`. If found, it removes the physical disk file (`os.remove`) and executes `await db.delete(document)`, which triggers database-level `CASCADE` on `document_chunks` table, purging all 1536-dim vector rows in the same transaction before returning HTTP `204 No Content`.
- **Laravel 12 Mental Mapping:** Matches an Eloquent controller method: `$doc = Document::where('org_id', $orgId)->findOrFail($id); Storage::delete($doc->path); $doc->delete(); return response()->noContent();`.

---

## 4. Master Cheat Sheet: Laravel 12 ↔ FastAPI Architecture Matrix

| Architectural Function | PHP / Laravel 12 Equivalent | Python / FastAPI Implementation in this Project |
| :--- | :--- | :--- |
| **Server / Execution Model** | PHP-FPM / Laravel Octane (Swoole/RoadRunner) | **Uvicorn** ASGI Asynchronous Event Loop (`uvicorn app.main:app`) |
| **App Entrypoint & Router** | `bootstrap/app.php` & `routes/api.php` | [app/main.py](file:///c:/laragon/www/fastapi-business-platform/app/main.py) & [app/api/v1/router.py](file:///c:/laragon/www/fastapi-business-platform/app/api/v1/router.py) |
| **Environment Configuration** | `.env` parsed by `config/*.php` (`env('KEY')`) | [app/core/config.py](file:///c:/laragon/www/fastapi-business-platform/app/core/config.py) using `pydantic_settings.BaseSettings` |
| **Request Validation** | `FormRequest` (`rules()`) | Pydantic v2 `BaseModel` (e.g. `RegisterRequest`, `AskRequest`) |
| **Response Transformation (DTO)** | `JsonResource` / Spatie `Laravel-Data` | Pydantic v2 `BaseModel` with `from_attributes=True` (`UserResponse`) |
| **Database ORM** | **Eloquent ORM** (ActiveRecord pattern) | **SQLAlchemy 2.0 Async** (Data Mapper pattern) |
| **Query Construction** | `User::where('email', $email)->first()` | `select(User).where(User.email == email)` via `db.execute()` |
| **Eager Loading Relationships** | `Document::with('chunks')->get()` | `select(Document).options(selectinload(Document.chunks))` |
| **Database Migrations** | `php artisan make:migration` / `migrate` | **Alembic** (`alembic revision --autogenerate`, `alembic upgrade head`) |
| **Password Hashing** | `Hash::make($pwd)` (Bcrypt/Argon2id) | `pwdlib[argon2]` via [app/core/security.py](file:///c:/laragon/www/fastapi-business-platform/app/core/security.py) (`hash_password`) |
| **API Authentication** | **Laravel Sanctum** / Passport Tokens | **PyJWT** (`create_access_token`) + OAuth2PasswordBearer |
| **Route Middleware** | `Route::middleware(['auth:sanctum'])` | `Depends(get_current_user)` parameter on route function |
| **Role Authorization** | `$this->authorize('admin')` / Gates / Policies | `Depends(get_current_active_admin)` route dependency |
| **Background Job Dispatch** | `ProcessDoc::dispatch($id)->onQueue('redis')` | `await redis_pool.enqueue_job('process_document_task', str(id))` |
| **Background Worker Daemon** | `php artisan queue:work redis` / Horizon | `arq app.worker.WorkerSettings` |
| **HTTP Client** | `Http::withToken($token)->post(...)` | `httpx.AsyncClient(timeout=30.0)` with `await client.post(...)` |
| **Vector Similarity Search** | Scout with pgvector driver or raw Eloquent scope | `DocumentChunk.embedding.cosine_distance(v).asc()` |
| **Automated Testing** | `php artisan test` (Pest / PHPUnit) | **Pytest** with `fastapi.testclient.TestClient` (`pytest -v`) |
| **Interactive API Documentation** | Scramble / L5-Swagger | Automatic OpenAPI / Swagger UI at `/docs` |
| **API Rate Limiting** | `throttle:10,1` / `RateLimiter::for(...)` | `slowapi` (`@limiter.limit("10/minute")`) |
| **Model Cascading Deletes** | Eloquent `$model->delete()` with DB cascades | SQLAlchemy `await db.delete(doc)` with `cascade="all, delete-orphan"` |

---

## 5. Essential Operations & Cheat Sheet Commands

All operational commands run via Docker Compose from the project root directory:

### 1. Container Lifecycle Commands
```bash
# Build and start all 4 services in detached mode
docker compose up --build -d

# Check live health status of all running containers
docker compose ps

# View unified real-time logs across API and Worker
docker compose logs -f api worker

# Stop all containers without deleting data volumes
docker compose down

# Wipe containers and database volumes (complete fresh reset)
docker compose down -v
```

### 2. Database Migrations (Alembic)
```bash
# Apply all pending migrations to PostgreSQL pgvector
docker compose exec api alembic upgrade head

# Inspect current database revision status
docker compose exec api alembic current

# Roll back the most recent migration
docker compose exec api alembic downgrade -1

# Generate a new migration script after modifying models
docker compose exec api alembic revision --autogenerate -m "describe_changes"
```

### 3. Automated Test Execution (Pytest)
```bash
# Execute the full automated test suite inside the container
docker compose exec api pytest -v

# Run only the AI, Vector Chunking, and RAG isolation tests
docker compose exec api pytest app/tests/test_ai.py -v

# Run tests with output printing enabled
docker compose exec api pytest -s
```

### 4. Switching AI Providers for RAG
The platform supports three AI provider modes via the `AI_PROVIDER` variable in `.env`:

| Mode | `.env` Configuration | Behavior |
| :--- | :--- | :--- |
| **Local Deterministic Mock (Default)** | `AI_PROVIDER=mock`<br>`OPENAI_API_KEY=""`<br>`GEMINI_API_KEY=""` | Generates normalized, reproducible 1536-dim mock embeddings and grounded synthetic answers without calling any external network APIs. Ideal for tests and offline development. |
| **OpenAI Cloud** | `AI_PROVIDER=openai`<br>`OPENAI_API_KEY=sk-proj-...` | Uses `text-embedding-3-small` (1536 dims) for embeddings and `gpt-4o-mini` for context-grounded RAG answers. |
| **Google Gemini Cloud** | `AI_PROVIDER=gemini`<br>`GEMINI_API_KEY=AIzaSy...` | Uses `gemini-embedding-001` (with `outputDimensionality=1536`) and `gemini-3.8-flash` for high-speed grounded RAG synthesis. |

After modifying `.env`, restart the API and Worker containers to apply changes:
```bash
docker compose restart api worker
```
