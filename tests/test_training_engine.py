import pytest
from fastapi import HTTPException

from app.services.training_engine import parse_identity_csv


def test_parse_identity_csv_requires_name_and_platform_email():
    content = "name,github_email\nAlice,alice@github.com\n"
    rows = parse_identity_csv(content)
    assert len(rows) == 1
    assert rows[0].name == "Alice"
    assert rows[0].github_email == "alice@github.com"


def test_parse_identity_csv_rejects_rows_without_platform_email():
    content = "name,github_email\nAlice,\n"
    with pytest.raises(HTTPException) as exc:
        parse_identity_csv(content)
    assert exc.value.status_code == 422


def test_parse_identity_csv_rejects_duplicate_emails_across_rows():
    content = (
        "name,github_email,google_docs_email\n"
        "Alice,shared@example.com,\n"
        "Bob,,shared@example.com\n"
    )
    with pytest.raises(HTTPException):
        parse_identity_csv(content)


def test_parse_identity_csv_allows_same_email_across_platforms_in_one_row():
    content = (
        "name,github_email,google_docs_email,google_meet_email\n"
        "Alice,alice@gmail.com,alice@school.edu,alice@school.edu\n"
    )
    rows = parse_identity_csv(content)
    assert len(rows) == 1
    assert rows[0].google_docs_email == rows[0].google_meet_email
