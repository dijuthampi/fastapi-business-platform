# FastAPI Business Platform — Multi-Tenant Applied AI & RAG Engine

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16_pgvector-336791.svg?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Redis 7](https://img.shields.io/badge/Redis-7_ARQ-DC382D.svg?logo=redis&logoColor=white)](https://redis.io/)
[![SQLAlchemy 2.0](https://img.shields.io/badge/SQLAlchemy-2.0_Async-D71F00.svg)](https://www.sqlalchemy.org/)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose_Topology-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Executive Overview

**FastAPI Business Platform** is an enterprise-grade, asynchronous modular monolith engineered for multi-tenant B2B SaaS applications. It combines modern async backend paradigms with an applied AI retrieval engine:

1. **Multi-Tenant Logical Isolation:** Complete tenant data boundary enforcement across authentication, relational database operations, and high-dimensional vector similarity queries.
2. **Asynchronous Ingestion Pipeline:** Non-blocking document ingestion (`HTTP 202 Accepted`) using an in-memory Redis message broker and dedicated ARQ background workers.
3. **Multimodal Document Understanding:** Native vision extraction of complex documents (scanned PDFs, client logos, charts, tables, numbers) via Google Gemini Native Multimodal API with automated, offline fallback to `pypdf`.
4. **pgvector Storage & Grounded RAG Engine:** Semantic chunking (`800` chars / `150` overlap) embedded into `1536`-dimensional vectors stored directly inside PostgreSQL 16 using HNSW cosine distance indexing (`<=>`). Context-grounded Question & Answering provides verifiable source citations (`document_id`, `filename`, `chunk_index`, `snippet`, `similarity_score`) while eliminating hallucinations.
5. **Production Hardening:** Tiered IP rate limiting powered by `slowapi`, cascading multi-tenant document and vector purging (`DELETE /api/v1/documents/{document_id}`), and automated unit/integration test coverage with an isolated mock AI provider.

---

## Key Architectural Highlights

### 1. Strict Multi-Tenant Isolation
- **Organization-Scoped Context:** Tenant boundaries are established upon registration (`POST /api/v1/auth/register`), provisioning an `Organization` UUID and creating the primary administrator `User`.
- **Stateless JWT Claims:** Cryptographically signed tokens (`HS256`, Argon2 password hashing via `pwdlib`) embed tenant context (`sub=user_id`, `org_id=organization_id`, `role=role`).
- **Query Boundary Enforcement:** Route dependencies (`Depends(get_current_user)`) inject the authenticated tenant user. Every relational query and vector search attaches an explicit `WHERE organization_id == current_user.organization_id` clause, mathematically preventing data leakage across organizations.

### 2. Async Document Ingestion & Multimodal Vision
- **Decoupled Job Queue:** Document uploads stream to disk at `/storage/uploads/{org_id}/{uuid}_{filename}` and enqueue an asynchronous task into Redis via ARQ (`process_document_task`), returning immediate confirmation (`202 Accepted`).
- **Gemini Native Document Understanding:** For PDF files, the background worker invokes `extract_text_from_pdf_multimodal` in [app/services/ai_service.py](file:///c:/laragon/www/fastapi-business-platform/app/services/ai_service.py). Gemini's vision models visually parse tables, logos, and graphic text into clean Markdown without requiring 500MB+ of heavyweight OS-level OCR dependencies (e.g., Tesseract).
- **Graceful Fallback:** Automatically falls back to standard `pypdf` extraction if the cloud model is unreachable, when `AI_PROVIDER != "gemini"`, or during automated test runs.

### 3. Semantic Chunking & pgvector Indexing
- **Sliding-Window Partitioning:** Document text is cleanly split into overlapping chunks (`CHUNK_SIZE=800`, `CHUNK_OVERLAP=150`) to maintain conceptual continuity across paragraph and sentence boundaries.
- **pgvector Integration:** Each chunk is converted into a normalized `1536`-dimensional vector (`Vector(1536)`). Chunks and embeddings are stored in PostgreSQL's `document_chunks` table, accelerated by an **HNSW** (Hierarchical Navigable Small World) index configured for cosine distance (`vector_cosine_ops`).

### 4. Grounded RAG Q&A Engine with High-Availability Cascade
- **Cosine Distance Search:** Incoming questions (`POST /api/v1/ai/ask`) are transformed into query vectors and matched against the tenant's chunk library using pgvector's `<=>` operator (`SELECT chunks ... ORDER BY embedding <=> query_vector ASC LIMIT top_k`).
- **Prompt Grounding:** Retrieved chunks construct a strict system prompt instructing the model to answer exclusively from the provided tenant documentation.
- **Resilient Model Cascade:** Production Gemini calls cycle through a tiered fallback cascade (`gemini-3.8-flash-lite` $\rightarrow$ `gemini-3.8-flash` $\rightarrow$ `gemini-3.5-flash`) with automatic retries on temporary `429` (rate limits) or `503` (capacity spikes).
- **Deterministic Mock Mode:** When `AI_PROVIDER="mock"` (default in `.env.example` and automated tests), vectors are generated deterministically using SHA-256 seeding and L2 normalization, allowing full end-to-end vector pipeline execution without external API keys or cloud dependencies.

### 5. Production Hardening
- **Anti-Abuse Rate Limiting:** Powered by `slowapi`, protecting sensitive endpoints (`POST /auth/login` and `POST /auth/register` capped at 10 req/min; `POST /ai/ask` capped at 20 req/min).
- **Atomic Deletion & Cascading Purge:** `DELETE /api/v1/documents/{document_id}` validates tenant ownership, removes physical storage files via `os.remove`, and deletes the database record—cascading automatically to delete all linked vector chunks.
- **Automated Test Suite:** Robust pytest test suite covering authentication, role authorization, upload validation, background worker tasks, rate limiting bypass, vector search, and strict cross-tenant isolation.

---

## System Architecture

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
│   │   • Uvicorn ASGI Server          │                 │   • ARQ Worker Event Loop         │   │
│   │   • JWT Auth & Multi-Tenancy     │                 │   • Multimodal PDF & pypdf        │   │
│   │   • slowapi Rate Limiting        │                 │   • Semantic Chunking (800/150)   │   │
│   │   • RAG Q&A Endpoint (/ai/ask)   │                 │   • Vector Embedding Ingestion    │   │
│   └─────────────────┬────────────────┘                 └─────────────────┬─────────────────┘   │
│                     │                                                    │                     │
│                     │ Enqueue Job ('process_document_task')              │ Pop Job             │
│                     ▼                                                    ▼                     │
│   ┌────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │                          redis:7-alpine (Queue & Cache Broker)                         │   │
│   │                                  Port: 6379                                            │   │
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
│   Shared Volume: ./storage:/app/storage (Isolated directories per organization UUID)           │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### End-to-End Execution Lifecycles

#### 1. Ingestion Pipeline
```
[POST /upload] ──> Stream to Disk ──> INSERT Doc(pending) ──> ARQ enqueue ──> HTTP 202 Accepted
                                                                  │
                                                                  ▼
[ARQ Worker] <── Gemini Multimodal PDF <── Chunking (800/150) <── Embedding (1536) <── INSERT Chunks
```

#### 2. Retrieval-Augmented Generation (RAG) Q&A Pipeline
```
[POST /ai/ask] ──> Generate Query Embedding ──> pgvector Cosine Search (<=>) ──> Join Docs (Tenant Scoped)
                                                                                       │
                                                                                       ▼
[AskResponse] <── Citations & Similarity <── Grounded Synthesis <── Top-K Context Chunks
```

---

## Project Structure

```
fastapi-business-platform/
├── alembic/                               # Database migrations (Alembic)
│   ├── env.py                             # Async migration runtime & pgvector registration
│   └── versions/                          # Linear schema revision versions
│       ├── 20261122_0001_create_organizations_and_users_tables.py
│       ├── 20261123_0002_create_documents_table.py
│       └── 20261124_0003_add_pgvector_and_document_chunks.py
├── alembic.ini                            # Alembic CLI configuration
├── app/                                   # Application source code
│   ├── api/                               # Transport & route controllers
│   │   ├── deps.py                        # Dependency injection (Auth, DB, Redis)
│   │   └── v1/                            # API Version 1 endpoints
│   │       ├── router.py                  # Master v1 API router
│   │       └── endpoints/
│   │           ├── ai.py                  # POST /ai/ask (RAG Q&A Engine)
│   │           ├── auth.py                # POST /register, POST /login, GET /me
│   │           ├── documents.py           # POST /upload, GET /, GET /{id}, DELETE /{id}
│   │           └── health.py              # GET /health (Liveness & DB ping)
│   ├── core/                              # Platform kernel & configuration
│   │   ├── config.py                      # Pydantic BaseSettings singleton (.env parser)
│   │   ├── database.py                    # Async engine, sessionmaker, Base, TimestampMixin, get_db
│   │   ├── limiter.py                     # slowapi rate limiter configuration
│   │   └── security.py                    # Argon2 password hashing & PyJWT token handling
│   ├── models/                            # SQLAlchemy 2.0 ORM entities
│   │   ├── __init__.py                    # Model registry export
│   │   ├── document.py                    # Document model (metadata & extracted text)
│   │   ├── document_chunk.py              # DocumentChunk model with Vector(1536)
│   │   ├── organization.py                # Organization model (Tenant boundary)
│   │   └── user.py                        # User model (Tenant member with role)
│   ├── schemas/                           # Pydantic v2 validation contracts & DTOs
│   │   ├── __init__.py                    # Schema registry export
│   │   ├── ai.py                          # AskRequest, SourceCitation, AskResponse
│   │   ├── auth.py                        # RegisterRequest, TokenResponse
│   │   ├── document.py                    # DocumentResponse, DocumentDetailResponse
│   │   ├── health.py                      # HealthCheckResponse
│   │   └── user.py                        # UserResponse
│   ├── services/                          # Pure domain business logic
│   │   ├── ai_service.py                  # Chunker, embeddings, multimodal vision & RAG
│   │   └── auth_service.py                # Organization & user registration, authentication
│   ├── tests/                             # Automated test suite (Pytest)
│   │   ├── conftest.py                    # Global test fixtures (forces AI_PROVIDER="mock", disables limiter)
│   │   ├── test_ai.py                     # Vector chunking, embedding, RAG & tenant isolation tests
│   │   ├── test_auth.py                   # Registration, login & JWT security tests
│   │   ├── test_documents.py              # Upload, storage, deletion & ARQ worker tests
│   │   └── test_health.py                 # Health probe & DB connectivity tests
│   ├── main.py                            # Application factory, lifespan, CORS & rate limit middleware
│   └── worker.py                          # ARQ worker entrypoint & background tasks
├── docs/                                  # Technical documentation
│   └── ARCHITECTURE_AND_GLOSSARY.md       # Deep-dive architecture manual & Laravel 12 comparison
├── storage/                               # Host volume mounted for uploads (/app/storage)
│   └── uploads/                           # Organized by tenant ID: {org_id}/{doc_id}_{filename}
├── .env.example                           # Example environment template
├── .env                                   # Active environment configuration
├── docker-compose.yml                     # 4-container production topology (api, worker, db, redis)
├── Dockerfile                             # Multi-stage production container build (Python 3.12-slim)
└── requirements.txt                       # Locked production dependencies
```

> **For in-depth architectural comparisons with Laravel 12, sequence diagrams, and glossary:**  
> Review [docs/ARCHITECTURE_AND_GLOSSARY.md](file:///c:/laragon/www/fastapi-business-platform/docs/ARCHITECTURE_AND_GLOSSARY.md).

---

## API Endpoints Reference

All API v1 routes are prefixed with `/api/v1`. Interactive Swagger documentation is available at `/docs`.

| Tag | Method | Endpoint | Auth | Rate Limit | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Health** | `GET` | `/api/v1/health` | Public | None | Liveness probe & PostgreSQL connection check. |
| **Auth** | `POST` | `/api/v1/auth/register` | Public | `10/minute` | Atomically creates a tenant Organization and initial admin User. |
| **Auth** | `POST` | `/api/v1/auth/login` | Public | `10/minute` | Authenticates user credentials and issues a JWT access token. |
| **Auth** | `GET` | `/api/v1/auth/me` | Bearer Token | None | Retrieves profile information for the current authenticated user. |
| **Documents** | `POST` | `/api/v1/documents/upload` | Bearer Token | None | Uploads a PDF/TXT/MD document, streams to disk, and enqueues worker task. |
| **Documents** | `GET` | `/api/v1/documents` | Bearer Token | None | Lists all documents belonging to the caller's organization. |
| **Documents** | `GET` | `/api/v1/documents/{document_id}` | Bearer Token | None | Retrieves document metadata and full extracted text. |
| **Documents** | `DELETE`| `/api/v1/documents/{document_id}` | Bearer Token | None | Removes disk file, deletes document, and cascades chunk/vector deletion. |
| **AI & RAG** | `POST` | `/api/v1/ai/ask` | Bearer Token | `20/minute` | Executes tenant-isolated vector search and grounded answer synthesis. |

---

## Quick Start (Local Setup with Docker)

### 1. Clone the Repository
```bash
git clone https://github.com/dijuthampi/fastapi-business-platform.git
cd fastapi-business-platform
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Review or modify your `.env` settings:
```env
# Database & Cache
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/fastapi_db
REDIS_URL=redis://redis:6379/0

# Storage & Upload Limits
UPLOAD_DIR=/app/storage/uploads
MAX_UPLOAD_SIZE_MB=10

# AI Configuration (mock | gemini | openai)
AI_PROVIDER=mock
GEMINI_API_KEY=
OPENAI_API_KEY=
EMBEDDING_DIMENSION=1536
CHUNK_SIZE=800
CHUNK_OVERLAP=150
```

> **Tip:** Leave `AI_PROVIDER=mock` to run offline without an external API key. Set `AI_PROVIDER=gemini` and supply your `GEMINI_API_KEY` for live multimodal extraction and grounded generation.

### 3. Build and Start the Containers
```bash
docker compose up --build -d
```
Verify all four containers are running and healthy:
```bash
docker compose ps
```

### 4. Execute Database Migrations
Run Alembic migrations inside the API container to enable the `vector` extension and generate all relational & vector tables:
```bash
docker compose exec api alembic upgrade head
```

### 5. Explore Interactive API Docs
Visit the interactive Swagger UI documentation:
- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Health Endpoint:** [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

### 6. Run the Test Suite
Execute the full automated pytest suite inside the container:
```bash
docker compose exec api python -m pytest -p no:cacheprovider
```

---

## Technology Stack

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.12, async/await coroutines)
- **ASGI Server:** [Uvicorn](https://www.uvicorn.org/) with `uvloop`
- **Database & Vector Search:** PostgreSQL 16 with [pgvector](https://github.com/pgvector/pgvector)
- **Async ORM:** [SQLAlchemy 2.0 Async](https://www.sqlalchemy.org/) with [asyncpg](https://github.com/MagicStack/asyncpg)
- **Database Migrations:** [Alembic](https://alembic.sqlalchemy.org/)
- **Background Task Processing:** [ARQ](https://github.com/samuelcolvin/arq) with [Redis 7](https://redis.io/)
- **Data Validation & Schemas:** [Pydantic v2](https://docs.pydantic.dev/)
- **Authentication:** [PyJWT](https://pyjwt.readthedocs.io/), [pwdlib](https://frankie567.github.io/pwdlib/) with Argon2
- **Document Processing:** [pypdf](https://pypdf.readthedocs.io/) & Google Gemini Multimodal Vision API
- **Rate Limiting:** [slowapi](https://github.com/laurentS/slowapi)
- **Testing:** [Pytest](https://docs.pytest.org/), `pytest-asyncio`, `httpx` (TestClient)

---

## License

This project is licensed under the [MIT License](LICENSE).
