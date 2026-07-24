from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

from app.models import IntegrationProvider
from app.services import integrations


def _user(user_id: str = "u1"):
    return SimpleNamespace(
        id=user_id,
        email="alice@school.edu",
        name="Alice",
    )


def test_frontend_redirect_builds_settings_url():
    url = integrations._frontend_redirect("github", "success")

    assert "integration=github" in url
    assert "status=success" in url
    assert url.startswith("https://")


@patch.dict(integrations.__dict__, {"GITHUB_CLIENT_ID": "gh-client"})
def test_build_github_connect_url_includes_client_and_state(monkeypatch):
    monkeypatch.setattr(
        integrations,
        "create_oauth_state",
        lambda user_id, provider: "signed-state",
    )

    url = integrations.build_github_connect_url(_user())

    assert "client_id=gh-client" in url
    assert "state=signed-state" in url
    assert url.startswith("https://github.com/login/oauth/authorize")


@patch.dict(integrations.__dict__, {"GITHUB_CLIENT_ID": ""})
def test_build_github_connect_url_requires_configuration():
    with pytest.raises(HTTPException) as exc:
        integrations.build_github_connect_url(_user())

    assert exc.value.status_code == 503


@patch.dict(integrations.__dict__, {"GOOGLE_CLIENT_ID": "google-client"})
def test_build_google_connect_url_includes_client_and_state(monkeypatch):
    monkeypatch.setattr(
        integrations,
        "create_oauth_state",
        lambda user_id, provider: "signed-state",
    )

    url = integrations.build_google_connect_url(_user())

    assert "client_id=google-client" in url
    assert "state=signed-state" in url
    assert url.startswith("https://accounts.google.com/")


@patch.dict(integrations.__dict__, {"GOOGLE_CLIENT_ID": ""})
def test_build_google_connect_url_requires_configuration():
    with pytest.raises(HTTPException) as exc:
        integrations.build_google_connect_url(_user())

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_handle_github_callback_redirects_on_invalid_state():
    db = AsyncMock()

    result = await integrations.handle_github_callback("code", "bad-state", db)

    assert result.endswith("integration=github&status=error")


@pytest.mark.asyncio
async def test_handle_github_callback_success(monkeypatch):
    db = AsyncMock()
    db.get = AsyncMock(return_value=_user())
    monkeypatch.setattr(
        integrations,
        "decode_oauth_state",
        lambda state: {"sub": "u1", "provider": IntegrationProvider.github.value},
    )
    monkeypatch.setattr(
        integrations,
        "_upsert_integration",
        AsyncMock(return_value=SimpleNamespace()),
    )

    token_response = MagicMock(status_code=200)
    token_response.json.return_value = {"access_token": "gh-token"}
    user_response = MagicMock(status_code=200)
    user_response.json.return_value = {"id": 42, "login": "alice", "email": None}
    emails_response = MagicMock(status_code=200)
    emails_response.json.return_value = [{"primary": True, "email": "alice@github.com"}]

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=token_response)
    mock_client.get = AsyncMock(side_effect=[user_response, emails_response])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(integrations.httpx, "AsyncClient", lambda: mock_client)

    result = await integrations.handle_github_callback("code", "state", db)

    assert result.endswith("integration=github&status=success")


@pytest.mark.asyncio
async def test_disconnect_integration_deletes_row():
    db = AsyncMock()
    db.execute = AsyncMock()

    await integrations.disconnect_integration(
        _user(), IntegrationProvider.github, db
    )

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_decrypted_access_token(monkeypatch):
    integration = SimpleNamespace(access_token_enc="encrypted")
    monkeypatch.setattr(integrations, "decrypt_token", lambda value: "plain-token")

    token = await integrations.get_decrypted_access_token(integration)

    assert token == "plain-token"


@pytest.mark.asyncio
async def test_refresh_google_token_if_needed_returns_current_token_when_valid(
    monkeypatch,
):
    integration = SimpleNamespace(
        access_token_enc="enc-current",
        refresh_token_enc="enc-refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db = AsyncMock()
    monkeypatch.setattr(integrations, "decrypt_token", lambda value: "current-token")

    token = await integrations.refresh_google_token_if_needed(integration, db)

    assert token == "current-token"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_get_integrations_status_returns_provider_flags():
    user = SimpleNamespace(id="u1", email="alice@school.edu")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    status = await integrations.get_integrations_status(user, db)

    assert status.github.connected is False
    assert status.google.connected is False


@pytest.mark.asyncio
async def test_handle_google_callback_redirects_on_invalid_state():
    db = AsyncMock()

    result = await integrations.handle_google_callback("code", "bad-state", db)

    assert result.endswith("integration=google&status=error")


@pytest.mark.asyncio
async def test_refresh_google_token_if_needed_refreshes_expired_token(monkeypatch):
    integration = SimpleNamespace(
        access_token_enc="enc-old",
        refresh_token_enc="enc-refresh",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db = AsyncMock()
    monkeypatch.setattr(integrations, "decrypt_token", lambda value: "refresh-token")
    monkeypatch.setattr(integrations, "encrypt_token", lambda value: f"enc-{value}")

    response = MagicMock(status_code=200)
    response.json.return_value = {"access_token": "new-token", "expires_in": 3600}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(integrations.httpx, "AsyncClient", lambda: mock_client)

    token = await integrations.refresh_google_token_if_needed(integration, db)

    assert token == "new-token"
    db.add.assert_called_once()
