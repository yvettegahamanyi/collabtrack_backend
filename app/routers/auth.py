import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

load_dotenv()

RESET_TOKEN_EXPIRE_MINUTES = int(os.getenv("RESET_TOKEN_EXPIRE_MINUTES", "30"))

from app.core.security import (
    create_access_token,
    generate_reset_otp,
    hash_password,
    hash_reset_otp,
    verify_password,
)
from app.database import get_db
from app.dependencies import get_current_user
from app.models import AccountStatus, PasswordResetToken, User
from app.schemas.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetData,
    RegisterRequest,
    RequestPasswordReset,
    ResetPassword,
    Token,
)
from app.schemas.response import ApiResponse, success
from app.schemas.user import UserOut
from app.services.email import send_password_reset_otp_email

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_PASSWORD_RESET_MESSAGE = (
    "If an account exists for that email, a verification code has been sent."
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
        account_status=AccountStatus.ACTIVE,
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

    user.has_logged_in = True
    if user.account_status == AccountStatus.PENDING:
        user.account_status = AccountStatus.ACTIVE
    db.add(user)

    token = create_access_token(subject=user.id)
    return success(
        data=Token(
            access_token=token,
            must_change_password=user.must_change_password,
        ),
        message="Login successful.",
        code=status.HTTP_200_OK,
    )


@router.post(
    "/request-password-reset",
    response_model=ApiResponse[PasswordResetData],
    summary="Request a password reset verification code",
)
async def request_password_reset(
    payload: RequestPasswordReset, db: AsyncSession = Depends(get_db)
):
    """Send a one-time verification code to the user's email."""
    user = await db.scalar(select(User).where(User.email == payload.email))

    if user is not None and user.is_active and not user.is_sandbox:
        await db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used.is_(False),
            )
            .values(used=True)
        )

        raw_otp, otp_hash = generate_reset_otp(user.id)
        reset = PasswordResetToken(
            user_id=user.id,
            token_hash=otp_hash,
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
        )
        db.add(reset)
        await db.flush()

        try:
            await send_password_reset_otp_email(
                to_email=user.email,
                otp=raw_otp,
                expire_minutes=RESET_TOKEN_EXPIRE_MINUTES,
            )
        except Exception:
            logger.exception(
                "Failed to send password reset email to %s", user.email
            )

    return success(
        data=PasswordResetData(),
        message=_PASSWORD_RESET_MESSAGE,
        code=status.HTTP_200_OK,
    )


@router.post(
    "/reset-password",
    response_model=ApiResponse[None],
    summary="Reset password using a verification code",
    responses={
        200: {"description": "Password reset successfully."},
        400: {"description": "Invalid or expired verification code."},
    },
)
async def reset_password(payload: ResetPassword, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not user.is_active or user.is_sandbox:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code.",
        )

    otp_hash = hash_reset_otp(user.id, payload.otp)
    reset = await db.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.token_hash == otp_hash,
            PasswordResetToken.used.is_(False),
            PasswordResetToken.expires_at >= datetime.now(timezone.utc),
        )
        .order_by(PasswordResetToken.created_at.desc())
    )

    if reset is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code.",
        )

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    reset.used = True
    db.add(user)
    return success(
        message="Password has been reset successfully.",
        code=status.HTTP_200_OK,
    )


@router.post(
    "/change-password",
    response_model=ApiResponse[None],
    summary="Change password for the authenticated user",
    responses={
        200: {"description": "Password changed successfully."},
        400: {"description": "Current password is incorrect."},
        401: {"description": "Missing or invalid token."},
    },
)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password.",
        )

    current_user.password_hash = hash_password(payload.new_password)
    current_user.must_change_password = False
    db.add(current_user)
    return success(
        message="Password changed successfully.",
        code=status.HTTP_200_OK,
    )
