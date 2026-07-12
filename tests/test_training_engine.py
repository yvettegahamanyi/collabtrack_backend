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


def test_parse_identity_csv_requires_name_column():
    content = "github_email\nalice@example.com\n"
    with pytest.raises(HTTPException):
        parse_identity_csv(content)


def test_parse_identity_csv_rejects_row_without_platform_email():
    content = "name,github_email\nAlice,\n"
    with pytest.raises(HTTPException):
        parse_identity_csv(content)


def test_parse_identity_csv_accepts_google_docs_header_variant():
    content = (
        "name,google docs email\n"
        "Alice,alice@example.com\n"
    )
    rows = parse_identity_csv(content)
    assert rows[0].google_docs_email == "alice@example.com"


def test_parse_identity_csv_rejects_empty_member_list():
    content = "name,github_email\n"
    with pytest.raises(HTTPException):
        parse_identity_csv(content)


def test_parse_identity_csv_skips_blank_name_rows():
    content = (
        "name,github_email\n"
        ",alice@example.com\n"
        "Bob,bob@example.com\n"
    )
    rows = parse_identity_csv(content)

    assert len(rows) == 1
    assert rows[0].name == "Bob"


def test_parse_identity_csv_normalizes_email_case_and_whitespace():
    content = (
        "name,github_email\n"
        "Alice,  Alice@Example.COM  \n"
    )
    rows = parse_identity_csv(content)

    assert rows[0].github_email == "alice@example.com"
