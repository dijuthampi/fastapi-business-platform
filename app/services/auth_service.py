"""Authentication and multi-tenant registration service layer."""

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import RegisterRequest


async def register_organization_and_admin(
    db: AsyncSession,
    payload: RegisterRequest,
) -> User:
    """Atomically create a new tenant Organization and its initial admin User.

    Args:
        db: Asynchronous SQLAlchemy database session.
        payload: Registration payload containing organization and user details.

    Returns:
        The newly created User model instance with role='admin'.

    Raises:
        HTTPException: 400 Bad Request if the organization slug or user email is already taken.
    """
    # 1. Check if organization slug is already registered
    org_stmt = select(Organization).where(Organization.slug == payload.organization_slug)
    org_result = await db.execute(org_stmt)
    existing_org = org_result.scalar_one_or_none()
    if existing_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An organization with slug '{payload.organization_slug}' already exists.",
        )

    # 2. Check if user email is already registered across the platform
    user_stmt = select(User).where(User.email == payload.email)
    user_result = await db.execute(user_stmt)
    existing_user = user_result.scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A user with email '{payload.email}' already exists.",
        )

    # 3. Create Tenant Organization with explicit UUID generated in memory
    organization_id = uuid.uuid4()
    organization = Organization(
        id=organization_id,
        name=payload.organization_name,
        slug=payload.organization_slug,
        is_active=True,
    )
    db.add(organization)
    await db.flush()  # Flush to stage in active database transaction

    # 4. Create Initial Administrator User with explicit UUIDs in memory
    user = User(
        id=uuid.uuid4(),
        organization_id=organization.id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role="admin",
        is_active=True,
    )
    db.add(user)

    # 5. Commit atomic transaction
    await db.commit()
    await db.refresh(user)

    return user


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> Optional[User]:
    """Authenticate a user by email and password.

    Args:
        db: Asynchronous SQLAlchemy database session.
        email: User email address.
        password: Plain text password to verify.

    Returns:
        User model if authentication succeeds and account is active, else None.
    """
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    if not user.is_active:
        return None

    return user
