from pydantic import BaseModel, EmailStr, Field

from app.schemas.user import UserOut


class RegisterRequest(BaseModel):
    email: EmailStr = Field(examples=["student@example.com"])
    password: str = Field(
        min_length=8, max_length=128, examples=["Secret123!"]
    )
    name: str | None = Field(default=None, examples=["Jane Doe"])


class LoginRequest(BaseModel):
    email: EmailStr = Field(examples=["student@example.com"])
    password: str = Field(
        min_length=1, max_length=128, examples=["Secret123!"]
    )


class Token(BaseModel):
    access_token: str = Field(examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."])
    token_type: str = "bearer"
    must_change_password: bool = False


class AuthResponse(Token):
    """Returned on register: the token plus the created user profile."""

    user: UserOut


class RequestPasswordReset(BaseModel):
    email: EmailStr = Field(examples=["student@example.com"])


class ResetPassword(BaseModel):
    email: EmailStr = Field(examples=["student@example.com"])
    otp: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="The 6-digit verification code sent to the user's email.",
        examples=["482913"],
    )
    new_password: str = Field(
        min_length=8, max_length=128, examples=["NewSecret123!"]
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(
        min_length=1, max_length=128, examples=["OldSecret123!"]
    )
    new_password: str = Field(
        min_length=8, max_length=128, examples=["NewSecret123!"]
    )


class PasswordResetData(BaseModel):
    """Empty payload; reset codes are delivered by email only."""
