from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import (
    create_access_token,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.database import get_db
from app.models import PasswordResetToken, User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    PasswordResetData,
    RegisterRequest,
    RequestPasswordReset,
    ResetPassword,
    Token,
)
from app.schemas.response import ApiResponse, success
from app.schemas.user import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

_PASSWORD_RESET_MESSAGE = (
    "If an account exists for that email, a reset token has been issued."
)


@router.post(
    "/register",
    response_model=ApiResponse[AuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
    responses={
        201: {"description": "Account created; access token returned."},
        409: {"description": "An account with this email already exists."},
    },
)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user and immediately return a login token.

    The role is left **unset**; it is chosen later during onboarding via
    `PATCH /users/me`. The returned `access_token` can be used right away as a
    Bearer token.
    """
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=payload.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    await db.flush()

    token = create_access_token(subject=user.id)
    return success(
        data=AuthResponse(access_token=token, user=UserOut.model_validate(user)),
        message="Account created successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.post(
    "/login",
    response_model=ApiResponse[Token],
    summary="Log in",
    responses={
        200: {"description": "Authenticated; access token returned."},
        401: {"description": "Incorrect email or password."},
        403: {"description": "This account has been deactivated."},
    },
)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Log in with JSON `email` and `password`.

    Copy `data.access_token` from the response, then click **Authorize** in
    Swagger and paste it as the Bearer token.
    """
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    token = create_access_token(subject=user.id)
    return success(
        data=Token(access_token=token),
        message="Login successful.",
        code=status.HTTP_200_OK,
    )


@router.post(
    "/request-password-reset",
    response_model=ApiResponse[PasswordResetData],
    summary="Request a password reset token",
)
async def request_password_reset(
    payload: RequestPasswordReset, db: AsyncSession = Depends(get_db)
):
    """Request a password reset token."""
    user = await db.scalar(select(User).where(User.email == payload.email))

    reset_token: str | None = None
    if user is not None and user.is_active:
        raw_token, token_hash = generate_reset_token()
        reset = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=settings.reset_token_expire_minutes),
        )
        db.add(reset)
        reset_token = raw_token

    return success(
        data=PasswordResetData(reset_token=reset_token),
        message=_PASSWORD_RESET_MESSAGE,
        code=status.HTTP_200_OK,
    )


@router.post(
    "/reset-password",
    response_model=ApiResponse[None],
    summary="Reset password using a token",
    responses={
        200: {"description": "Password reset successfully."},
        400: {"description": "Invalid or expired reset token."},
    },
)
async def reset_password(payload: ResetPassword, db: AsyncSession = Depends(get_db)):
    token_hash = hash_reset_token(payload.token)
    reset = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )

    if (
        reset is None
        or reset.used
        or reset.expires_at < datetime.now(timezone.utc)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    user = await db.get(User, reset.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    user.password_hash = hash_password(payload.new_password)
    reset.used = True
    return success(
        message="Password has been reset successfully.",
        code=status.HTTP_200_OK,
    )
