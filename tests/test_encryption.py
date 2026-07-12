import pytest
from cryptography.fernet import Fernet

from app.core import encryption


def test_encrypt_decrypt_roundtrip(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(encryption, "TOKEN_ENCRYPTION_KEY", key)

    ciphertext = encryption.encrypt_token("oauth-secret-token")
    assert encryption.decrypt_token(ciphertext) == "oauth-secret-token"


def test_decrypt_invalid_token_raises(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(encryption, "TOKEN_ENCRYPTION_KEY", key)

    with pytest.raises(ValueError, match="Failed to decrypt token"):
        encryption.decrypt_token("not-valid-ciphertext")


def test_missing_encryption_key_raises(monkeypatch):
    monkeypatch.setattr(encryption, "TOKEN_ENCRYPTION_KEY", "")

    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
        encryption.encrypt_token("value")
