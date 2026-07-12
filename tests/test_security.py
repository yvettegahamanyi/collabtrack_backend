import io
from datetime import datetime, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.core import security


def test_hash_and_verify_password_roundtrip():
    hashed = security.hash_password("Secret123!")
    assert security.verify_password("Secret123!", hashed)
    assert not security.verify_password("WrongPassword", hashed)


def test_verify_password_rejects_empty_hash():
    assert security.verify_password("Secret123!", "") is False


def test_verify_password_rejects_invalid_hash():
    assert security.verify_password("Secret123!", "not-a-bcrypt-hash") is False


def test_generate_reset_token_returns_hashable_pair():
    raw_token, token_hash = security.generate_reset_token()

    assert raw_token
    assert token_hash == security.hash_reset_token(raw_token)


def test_create_and_decode_access_token():
    token = security.create_access_token("user-123", expires_minutes=30)
    payload = security.decode_access_token(token)

    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_decode_access_token_rejects_invalid_token():
    with pytest.raises(jwt.PyJWTError):
        security.decode_access_token("not-a-valid-token")


def test_reset_token_hash_is_stable():
    raw = "reset-token-value"
    assert security.hash_reset_token(raw) == security.hash_reset_token(raw)


def test_reset_otp_is_six_digits():
    raw_otp, otp_hash = security.generate_reset_otp("user-1")
    assert len(raw_otp) == 6
    assert raw_otp.isdigit()
    assert otp_hash == security.hash_reset_otp("user-1", raw_otp)


def test_invite_token_hash_matches():
    raw, hashed = security.generate_invite_token()
    assert security.hash_invite_token(raw) == hashed


def test_oauth_state_roundtrip():
    state = security.create_oauth_state("user-1", "github")
    payload = security.decode_oauth_state(state)

    assert payload["sub"] == "user-1"
    assert payload["provider"] == "github"


def test_oauth_state_rejects_access_token():
    access = security.create_access_token("user-1")
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_oauth_state(access)
