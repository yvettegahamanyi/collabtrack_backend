from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
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
    MessageResponse,
    PasswordResetRequestResponse,
    RegisterRequest,
    RequestPasswordReset,
    ResetPassword,
    Token,
)
from app.schemas.user import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AuthResponse,
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
    await db.flush()  # populate user.id without ending the transaction

    token = create_access_token(subject=user.id)
    return AuthResponse(access_token=token, user=UserOut.model_validate(user))


@router.post(
    "/login",
    response_model=Token,
    summary="Log in (OAuth2 password flow)",
    responses={
        200: {"description": "Authenticated; access token returned."},
        401: {"description": "Incorrect email or password."},
        403: {"description": "This account has been deactivated."},
    },
)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """OAuth2 password login.

    Send **form-encoded** fields `username` (the email) and `password`.
    In Swagger, use the **Authorize** button rather than calling this directly.
    """
    user = await db.scalar(select(User).where(User.email == form_data.username))
    if user is None or not verify_password(form_data.password, user.password_hash):
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
    return Token(access_token=token)


@router.post(
    "/request-password-reset",
    response_model=PasswordResetRequestResponse,
    summary="Request a password reset token",
)
async def request_password_reset(
    payload: RequestPasswordReset, db: AsyncSession = Depends(get_db)
):
    """Request a password reset token.

    Always returns a generic message so we don't leak which emails exist.
    Until an email service is wired up, the raw token is returned in the
    `reset_token` field for development convenience.
    """
    user = await db.scalar(select(User).where(User.email == payload.email))

    generic = PasswordResetRequestResponse(
        message="If an account exists for that email, a reset token has been issued."
    )

    if user is None or not user.is_active:
        return generic

    raw_token, token_hash = generate_reset_token()
    reset = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=settings.reset_token_expire_minutes),
    )
    db.add(reset)

    # Dev convenience: return the raw token directly (no email service yet).
    generic.reset_token = raw_token
    return generic


@router.post(
    "/reset-password",
    response_model=MessageResponse,
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
    return MessageResponse(message="Password has been reset successfully.")
