from unittest.mock import AsyncMock

from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.main import app

client = TestClient(app)


def test_health_check_endpoint_schema() -> None:
    """Test that the health endpoint returns 200 OK and includes all expected fields."""
    response = client.get(f"{settings.API_V1_STR}/health")

    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["status"] in ["ok", "degraded"]
    assert data["app_name"] == settings.PROJECT_NAME
    assert data["version"] == settings.VERSION
    assert data["environment"] == settings.ENVIRONMENT
    assert data["database"] in ["connected", "disconnected"]
    assert "timestamp" in data


def test_health_check_database_connected() -> None:
    """Test health check reports 'connected' and 'ok' when database query succeeds."""
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=None)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get(f"{settings.API_V1_STR}/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["database"] == "connected"
        assert data["status"] == "ok"
    finally:
        app.dependency_overrides.clear()


def test_health_check_database_disconnected() -> None:
    """Test health check reports 'disconnected' and 'degraded' when database query fails."""
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=ConnectionRefusedError("Database unreachable"))

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get(f"{settings.API_V1_STR}/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["database"] == "disconnected"
        assert data["status"] == "degraded"
    finally:
        app.dependency_overrides.clear()
