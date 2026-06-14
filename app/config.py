from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    database_url: str
    app_name: str = "CollabTrack"
    environment: str = "development"
    debug: bool = False

    # Security / JWT
    secret_key: str = "CHANGE_ME_IN_ENV"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 1 day
    reset_token_expire_minutes: int = 30

    # Admin seed (used by scripts/seed_admin.py)
    admin_email: str = "admin@collabtrack.com"
    admin_password: str = "ChangeMe123!"
    admin_name: str = "CollabTrack Admin"

    # Base URL for invite links (no trailing slash)
    frontend_url: str = "http://localhost:3000"

    # GitHub OAuth
    github_client_id: str = ""
    github_client_secret: str = ""
    github_callback_url: str = "http://localhost:8000/integrations/github/callback"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_callback_url: str = "http://localhost:8000/integrations/google/callback"

    # Fernet key for encrypting OAuth tokens at rest (generate with cryptography.fernet.Fernet.generate_key())
    token_encryption_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, v: str) -> str:
        """Normalize Railway-style URLs to the asyncpg driver scheme.

        Railway hands out URLs like ``postgresql://`` or ``postgres://``;
        the async engine needs ``postgresql+asyncpg://``.
        """
        if v.startswith("postgresql+asyncpg://"):
            return v
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
