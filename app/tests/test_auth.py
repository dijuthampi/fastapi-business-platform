"""Automated integration and unit tests for Multi-Tenant Authentication & JWT Security."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.main import app
from app.models.organization import Organization
from app.models.user import User

client = TestClient(app)


# ---------------------------------------------------------------------------
# Security Utilities Unit Tests
# ---------------------------------------------------------------------------

def test_password_hashing_and_verification() -> None:
    """Test that password hashing produces valid verifiable hashes."""
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_jwt_token_creation_and_decoding() -> None:
    """Test creating and decoding a JWT token with tenant and role claims."""
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    role = "admin"

    token = create_access_token(subject=user_id, organization_id=org_id, role=role)
    assert isinstance(token, str)

    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["org_id"] == str(org_id)
    assert payload["role"] == role
    assert "exp" in payload
    assert "iat" in payload


# ---------------------------------------------------------------------------
# Registration Tests (POST /api/v1/auth/register)
# ---------------------------------------------------------------------------

def test_register_organization_and_admin_success() -> None:
    """Test successful registration of tenant organization and admin user (201)."""
    mock_db = AsyncMock()
    # db.add is synchronous in SQLAlchemy AsyncSession; use MagicMock to avoid unawaited coroutine warnings
    mock_db.add = MagicMock()

    # Simulate no existing org and no existing user (empty results)
    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = None

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(side_effect=[org_result, user_result])

    # Assign generated timestamp on refresh if not present
    async def mock_refresh(instance: User):
        if not hasattr(instance, "created_at") or not instance.created_at:
            instance.created_at = datetime.now(timezone.utc)

    mock_db.refresh = AsyncMock(side_effect=mock_refresh)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        payload = {
            "organization_name": "Acme Global",
            "organization_slug": "acme-global",
            "email": "alice@acmeglobal.com",
            "password": "SecurePassword123!",
            "full_name": "Alice Johnson",
        }
        response = client.post(f"{settings.API_V1_STR}/auth/register", json=payload)

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["email"] == "alice@acmeglobal.com"
        assert data["full_name"] == "Alice Johnson"
        assert data["role"] == "admin"
        assert data["is_active"] is True
        assert "id" in data
        assert "organization_id" in data
        assert "created_at" in data
        # Ensure db.add was invoked synchronously for both entities
        assert mock_db.add.call_count == 2
    finally:
        app.dependency_overrides.clear()


def test_register_duplicate_slug_rejected() -> None:
    """Test registration fails with 400 Bad Request when organization slug exists."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    existing_org = Organization(
        id=uuid.uuid4(),
        name="Existing Org",
        slug="existing-slug",
    )
    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = existing_org
    mock_db.execute = AsyncMock(return_value=org_result)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        payload = {
            "organization_name": "Existing Org",
            "organization_slug": "existing-slug",
            "email": "newadmin@existing.com",
            "password": "SecurePassword123!",
            "full_name": "Bob Smith",
        }
        response = client.post(f"{settings.API_V1_STR}/auth/register", json=payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "existing-slug" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_register_duplicate_email_rejected() -> None:
    """Test registration fails with 400 Bad Request when email already exists."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    # Org does not exist, but user email does
    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = None

    existing_user = User(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="duplicate@acme.com",
        hashed_password="hash",
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = existing_user

    mock_db.execute = AsyncMock(side_effect=[org_result, user_result])

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        payload = {
            "organization_name": "Brand New Org",
            "organization_slug": "brand-new-org",
            "email": "duplicate@acme.com",
            "password": "SecurePassword123!",
            "full_name": "Charlie Brown",
        }
        response = client.post(f"{settings.API_V1_STR}/auth/register", json=payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "duplicate@acme.com" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Login Tests (POST /api/v1/auth/login)
# ---------------------------------------------------------------------------

def test_login_success() -> None:
    """Test login with valid OAuth2 credentials returns 200 and JWT access token."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    password = "CorrectPassword123!"
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    test_user = User(
        id=user_id,
        organization_id=org_id,
        email="admin@example.com",
        hashed_password=hash_password(password),
        full_name="Admin User",
        role="admin",
        is_active=True,
    )

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = test_user
    mock_db.execute = AsyncMock(return_value=user_result)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        # OAuth2 password request form requires application/x-www-form-urlencoded
        login_data = {
            "username": "admin@example.com",
            "password": password,
        }
        response = client.post(f"{settings.API_V1_STR}/auth/login", data=login_data)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

        # Verify token contents
        payload = decode_access_token(data["access_token"])
        assert payload["sub"] == str(user_id)
        assert payload["org_id"] == str(org_id)
        assert payload["role"] == "admin"
    finally:
        app.dependency_overrides.clear()


def test_login_invalid_password_rejected() -> None:
    """Test login fails with 401 Unauthorized when password is incorrect."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    test_user = User(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="admin@example.com",
        hashed_password=hash_password("RealPassword123!"),
        role="admin",
        is_active=True,
    )

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = test_user
    mock_db.execute = AsyncMock(return_value=user_result)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        login_data = {
            "username": "admin@example.com",
            "password": "WrongPassword999!",
        }
        response = client.post(f"{settings.API_V1_STR}/auth/login", data=login_data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["detail"] == "Incorrect email or password"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Protected Route Tests (GET /api/v1/auth/me)
# ---------------------------------------------------------------------------

def test_get_me_with_valid_bearer_token() -> None:
    """Test accessing /auth/me with a valid Bearer token returns current user profile."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    test_user = User(
        id=user_id,
        organization_id=org_id,
        email="me@example.com",
        hashed_password="some_hashed_password",
        full_name="Me User",
        role="admin",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = test_user
    mock_db.execute = AsyncMock(return_value=user_result)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        token = create_access_token(subject=user_id, organization_id=org_id, role="admin")
        headers = {"Authorization": f"Bearer {token}"}

        response = client.get(f"{settings.API_V1_STR}/auth/me", headers=headers)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(user_id)
        assert data["email"] == "me@example.com"
        assert data["full_name"] == "Me User"
        assert data["role"] == "admin"
        assert data["is_active"] is True
    finally:
        app.dependency_overrides.clear()


def test_get_me_missing_token_unauthorized() -> None:
    """Test accessing /auth/me without an Authorization header returns 401 Unauthorized."""
    response = client.get(f"{settings.API_V1_STR}/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Not authenticated"


def test_get_me_invalid_token_unauthorized() -> None:
    """Test accessing /auth/me with an invalid token returns 401 Unauthorized."""
    headers = {"Authorization": "Bearer invalid.token.payload"}
    response = client.get(f"{settings.API_V1_STR}/auth/me", headers=headers)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
