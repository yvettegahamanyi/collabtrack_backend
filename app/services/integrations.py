import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

load_dotenv()

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_CALLBACK_URL = os.getenv(
    "GITHUB_CALLBACK_URL",
    "http://localhost:8000/integrations/github/callback",
)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_CALLBACK_URL = os.getenv(
    "GOOGLE_CALLBACK_URL",
    "http://localhost:8000/integrations/google/callback",
)
FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "https://collabtrackfrontend-production.up.railway.app",
)

from app.core.encryption import decrypt_token, encrypt_token
from app.core.security import create_oauth_state, decode_oauth_state
from app.models import IntegrationProvider, User, UserIntegration
from app.schemas.integration import IntegrationsStatusOut, ProviderStatus

_GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_API = "https://api.github.com"

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

_GITHUB_SCOPES = "read:user user:email repo"
_GOOGLE_SCOPES = (
    "openid email profile "
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/documents.readonly "
    "https://www.googleapis.com/auth/drive.activity.readonly"
)

_GITHUB_REPO_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?(?:#.*)?$"
)
_GOOGLE_DOC_RE = re.compile(
    r"^https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)"
)


def parse_github_repo_url(url: str) -> tuple[str, str]:
    match = _GITHUB_REPO_RE.match(url.strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository URL.",
        )
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def parse_google_doc_url(url: str) -> str:
    match = _GOOGLE_DOC_RE.match(url.strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Google Docs URL.",
        )
    return match.group(1)


def _provider_status(integration: UserIntegration | None) -> ProviderStatus:
    if integration is None:
        return ProviderStatus(connected=False)
    return ProviderStatus(
        connected=True,
        login=integration.provider_login,
        email=integration.provider_email,
        email_matched=integration.email_matched,
        connected_at=integration.connected_at,
    )


async def get_user_integration(
    db: AsyncSession, user_id: str, provider: IntegrationProvider
) -> UserIntegration | None:
    return await db.scalar(
        select(UserIntegration).where(
            UserIntegration.user_id == user_id,
            UserIntegration.provider == provider.value,
        )
    )


async def get_integrations_status(
    user: User, db: AsyncSession
) -> IntegrationsStatusOut:
    github = await get_user_integration(db, user.id, IntegrationProvider.github)
    google = await get_user_integration(db, user.id, IntegrationProvider.google)
    return IntegrationsStatusOut(
        user_id=user.id,
        user_email=user.email,
        github=_provider_status(github),
        google=_provider_status(google),
    )


def build_github_connect_url(user: User) -> str:
    if not GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub OAuth is not configured.",
        )
    state = create_oauth_state(user.id, IntegrationProvider.github.value)
    params = urlencode(
        {
            "client_id": GITHUB_CLIENT_ID,
            "redirect_uri": GITHUB_CALLBACK_URL,
            "scope": _GITHUB_SCOPES,
            "state": state,
        }
    )
    return f"{_GITHUB_AUTH_URL}?{params}"


def build_google_connect_url(user: User) -> str:
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured.",
        )
    state = create_oauth_state(user.id, IntegrationProvider.google.value)
    params = urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_CALLBACK_URL,
            "response_type": "code",
            "scope": _GOOGLE_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{_GOOGLE_AUTH_URL}?{params}"


async def _upsert_integration(
    db: AsyncSession,
    *,
    user: User,
    provider: IntegrationProvider,
    provider_user_id: str,
    provider_login: str | None,
    provider_email: str | None,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
) -> UserIntegration:
    email_matched = (
        provider_email is not None
        and provider_email.lower() == user.email.lower()
    )
    existing = await get_user_integration(db, user.id, provider)
    if existing:
        existing.provider_user_id = provider_user_id
        existing.provider_login = provider_login
        existing.provider_email = provider_email
        existing.email_matched = email_matched
        existing.access_token_enc = encrypt_token(access_token)
        if refresh_token:
            existing.refresh_token_enc = encrypt_token(refresh_token)
        existing.expires_at = expires_at
        existing.connected_at = datetime.now(timezone.utc)
        db.add(existing)
        return existing

    integration = UserIntegration(
        user_id=user.id,
        provider=provider,
        provider_user_id=provider_user_id,
        provider_login=provider_login,
        provider_email=provider_email,
        email_matched=email_matched,
        access_token_enc=encrypt_token(access_token),
        refresh_token_enc=encrypt_token(refresh_token) if refresh_token else None,
        expires_at=expires_at,
    )
    db.add(integration)
    return integration


async def handle_github_callback(code: str, state: str, db: AsyncSession) -> str:
    try:
        payload = decode_oauth_state(state)
    except jwt.PyJWTError:
        return _frontend_redirect("github", "error")

    if payload.get("provider") != IntegrationProvider.github.value:
        return _frontend_redirect("github", "error")

    user = await db.get(User, payload["sub"])
    if user is None:
        return _frontend_redirect("github", "error")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            _GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_CALLBACK_URL,
            },
        )
        if token_resp.status_code != 200:
            return _frontend_redirect("github", "error")
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return _frontend_redirect("github", "error")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }
        user_resp = await client.get(f"{_GITHUB_API}/user", headers=headers)
        if user_resp.status_code != 200:
            return _frontend_redirect("github", "error")
        gh_user = user_resp.json()

        emails_resp = await client.get(f"{_GITHUB_API}/user/emails", headers=headers)
        primary_email = None
        if emails_resp.status_code == 200:
            for entry in emails_resp.json():
                if entry.get("primary"):
                    primary_email = entry.get("email")
                    break

    await _upsert_integration(
        db,
        user=user,
        provider=IntegrationProvider.github,
        provider_user_id=str(gh_user["id"]),
        provider_login=gh_user.get("login"),
        provider_email=primary_email or gh_user.get("email"),
        access_token=access_token,
        refresh_token=None,
        expires_at=None,
    )
    return _frontend_redirect("github", "success")


async def handle_google_callback(code: str, state: str, db: AsyncSession) -> str:
    try:
        payload = decode_oauth_state(state)
    except jwt.PyJWTError:
        return _frontend_redirect("google", "error")

    if payload.get("provider") != IntegrationProvider.google.value:
        return _frontend_redirect("google", "error")

    user = await db.get(User, payload["sub"])
    if user is None:
        return _frontend_redirect("google", "error")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": GOOGLE_CALLBACK_URL,
            },
        )
        if token_resp.status_code != 200:
            return _frontend_redirect("google", "error")
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return _frontend_redirect("google", "error")

        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        expires_at = None
        if expires_in:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

        user_resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_resp.status_code != 200:
            return _frontend_redirect("google", "error")
        g_user = user_resp.json()

    await _upsert_integration(
        db,
        user=user,
        provider=IntegrationProvider.google,
        provider_user_id=g_user["id"],
        provider_login=None,
        provider_email=g_user.get("email"),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )
    return _frontend_redirect("google", "success")


async def disconnect_integration(
    user: User, provider: IntegrationProvider, db: AsyncSession
) -> None:
    await db.execute(
        delete(UserIntegration).where(
            UserIntegration.user_id == user.id,
            UserIntegration.provider == provider.value,
        )
    )


def _frontend_redirect(integration: str, status_value: str) -> str:
    base = FRONTEND_URL.rstrip("/")
    return f"{base}/student/settings?integration={integration}&status={status_value}"


async def get_decrypted_access_token(
    integration: UserIntegration,
) -> str:
    return decrypt_token(integration.access_token_enc)


async def refresh_google_token_if_needed(
    integration: UserIntegration, db: AsyncSession
) -> str:
    now = datetime.now(timezone.utc)
    if integration.expires_at and integration.expires_at > now:
        return decrypt_token(integration.access_token_enc)

    if not integration.refresh_token_enc:
        return decrypt_token(integration.access_token_enc)

    refresh_token = decrypt_token(integration.refresh_token_enc)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to refresh Google access token.",
            )
        data = resp.json()

    access_token = data["access_token"]
    integration.access_token_enc = encrypt_token(access_token)
    expires_in = data.get("expires_in")
    if expires_in:
        integration.expires_at = now + timedelta(seconds=int(expires_in))
    db.add(integration)
    return access_token
