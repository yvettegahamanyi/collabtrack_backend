from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.integrations import _provider_status, parse_github_repo_url, parse_google_doc_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/org/repo", ("org", "repo")),
        ("https://github.com/org/repo.git", ("org", "repo")),
        ("http://www.github.com/org/repo/", ("org", "repo")),
    ],
)
def test_parse_github_repo_url_accepts_valid_urls(url, expected):
    assert parse_github_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/org/repo",
        "https://github.com/org",
        "not-a-url",
    ],
)
def test_parse_github_repo_url_rejects_invalid_urls(url):
    with pytest.raises(HTTPException, match="Invalid GitHub repository URL"):
        parse_github_repo_url(url)


@pytest.mark.parametrize(
    "url,doc_id",
    [
        (
            "https://docs.google.com/document/d/abc123XYZ/edit",
            "abc123XYZ",
        ),
        (
            "https://docs.google.com/document/d/doc-id_with-chars",
            "doc-id_with-chars",
        ),
    ],
)
def test_parse_google_doc_url_accepts_valid_urls(url, doc_id):
    assert parse_google_doc_url(url) == doc_id


def test_parse_google_doc_url_rejects_invalid_url():
    with pytest.raises(HTTPException, match="Invalid Google Docs URL"):
        parse_google_doc_url("https://drive.google.com/file/d/abc/view")


def test_provider_status_for_missing_integration():
    status = _provider_status(None)

    assert status.connected is False
    assert status.login is None


def test_provider_status_for_connected_integration():
    integration = SimpleNamespace(
        provider_login="alice",
        provider_email="alice@example.com",
        email_matched=True,
        connected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    status = _provider_status(integration)

    assert status.connected is True
    assert status.login == "alice"
    assert status.email_matched is True
