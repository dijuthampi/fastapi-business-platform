"""Authentication API endpoints: registration, OAuth2 login, and current user profile."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.limiter import limiter
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenResponse
from app.schemas.user import UserResponse
from app.services import auth_service

router = APIRouter()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new tenant organization and admin user",
    description=(
        "Atomically registers a new business tenant organization and creates its initial "
        "administrator user. Returns the registered admin user profile."
    ),
)
@limiter.limit("10/minute")
async def register(
    request: Request,
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Create tenant organization and initial admin user."""
    return await auth_service.register_organization_and_admin(db=db, payload=payload)


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="OAuth2 compatible token login",
    description=(
        "Authenticates a user via email (username field) and password. "
        "Returns a JWT Bearer access token encoded with tenant and role claims."
    ),
)
@limiter.limit("10/minute")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """OAuth2 password login returning a JWT access token."""
    user = await auth_service.authenticate_user(
        db=db,
        email=form_data.username,
        password=form_data.password,
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        subject=user.id,
        organization_id=user.organization_id,
        role=user.role,
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
    )


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get authenticated user profile",
    description="Returns the profile details of the currently authenticated user.",
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> User:
    """Return the currently authenticated user's profile."""
    return current_user
