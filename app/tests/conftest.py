"""Global pytest configuration and fixtures for automated test suites."""

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def force_mock_ai_provider(monkeypatch: pytest.MonkeyPatch):
    """Force settings.AI_PROVIDER to 'mock' for all automated test runs.

    Ensures that the entire test suite consistently executes deterministic, offline
    mock AI chunking, embedding, and RAG synthesis without making external network calls,
    even when the local .env file configures AI_PROVIDER='gemini' or 'openai' for live development.
    """
    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    yield
