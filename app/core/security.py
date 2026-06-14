import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# bcrypt only uses the first 72 bytes of a password.
_BCRYPT_MAX_BYTES = 72


def _truncate(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_truncate(password), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    """Create a signed JWT access token for the given subject (user id)."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT access token. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


def generate_reset_token() -> tuple[str, str]:
    """Return a (raw_token, token_hash) pair for password resets.

    The raw token is returned to the user; only its hash is stored.
    """
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_reset_token(raw_token)


def hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_invite_token() -> tuple[str, str]:
    """Return (raw_token, token_hash) for group invitations."""
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_invite_token(raw_token)


def hash_invite_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_oauth_state(user_id: str, provider: str) -> str:
    """Signed state token passed through the OAuth redirect flow."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    payload = {"sub": user_id, "provider": provider, "exp": expire, "type": "oauth_state"}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_oauth_state(state: str) -> dict:
    payload = jwt.decode(
        state, settings.secret_key, algorithms=[settings.algorithm]
    )
    if payload.get("type") != "oauth_state":
        raise jwt.InvalidTokenError("Invalid OAuth state token.")
    return payload
