from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_health_check_endpoint() -> None:
    """Test that the /api/v1/health endpoint returns 200 OK and valid health metadata."""
    response = client.get(f"{settings.API_V1_STR}/health")

    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["status"] == "ok"
    assert data["app_name"] == settings.PROJECT_NAME
    assert data["version"] == settings.VERSION
    assert data["environment"] == settings.ENVIRONMENT
    assert "timestamp" in data
