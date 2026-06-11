from pydantic import BaseModel, EmailStr, Field

from app.schemas.user import UserOut


class RegisterRequest(BaseModel):
    email: EmailStr = Field(examples=["student@example.com"])
    password: str = Field(
        min_length=8, max_length=128, examples=["Secret123!"]
    )
    name: str | None = Field(default=None, examples=["Jane Doe"])


class Token(BaseModel):
    access_token: str = Field(examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."])
    token_type: str = "bearer"


class AuthResponse(Token):
    """Returned on register: the token plus the created user profile."""

    user: UserOut


class RequestPasswordReset(BaseModel):
    email: EmailStr = Field(examples=["student@example.com"])


class ResetPassword(BaseModel):
    token: str = Field(
        description="The raw reset token issued by `/auth/request-password-reset`.",
        examples=["QbTzs_3MFuQ1amtnk86srmR_kEuwl6gdctkHZz0rOXQ"],
    )
    new_password: str = Field(
        min_length=8, max_length=128, examples=["NewSecret123!"]
    )


class PasswordResetData(BaseModel):
    """DEV ONLY: reset_token is returned until email delivery is wired up."""

    reset_token: str | None = Field(
        default=None,
        description="DEV ONLY: the raw reset token. Removed once email is wired up.",
        examples=["QbTzs_3MFuQ1amtnk86srmR_kEuwl6gdctkHZz0rOXQ"],
    )
