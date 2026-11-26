"""Global pytest configuration and fixtures for automated test suites."""

import pytest

from app.core.config import settings
from app.core.limiter import limiter


@pytest.fixture(autouse=True)
def force_mock_ai_provider(monkeypatch: pytest.MonkeyPatch):
    """Force settings.AI_PROVIDER to 'mock' and disable rate limiting for all automated test runs.

    Ensures that the entire test suite consistently executes deterministic, offline
    mock AI chunking, embedding, and RAG synthesis without making external network calls,
    and fast automated test execution never triggers 429 Too Many Requests.
    """
    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    limiter.enabled = False
    yield
    limiter.enabled = True
