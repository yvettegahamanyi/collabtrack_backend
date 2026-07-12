"""Parametrized edge-case tests for meeting_parser.

Run with verbose IDs for capstone edge-case tables:
    pytest tests/test_meeting_parser.py -m edge_case -v --tb=no

Scoped coverage for this module only (avoids misleading ~5% app total):
    pytest tests/test_meeting_parser.py -m edge_case -v \\
        -o addopts='-ra --strict-markers' \\
        --cov=app.services.meeting_parser --cov-report=term-missing
"""

import pytest

from app.services.meeting_parser import (
    MeetingParseError,
    parse_attendance_csv,
    parse_facilitator,
    parse_member_list,
    parse_transcript_or_chat,
    last_timestamp_minutes,
    unique_display_names,
    AttendanceRecord,
)


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "value,expected",
    [
        ("Yes", True),
        ("yes", True),
        ("YES", True),
        ("True", True),
        ("true", True),
        ("1", True),
        ("No", False),
        ("no", False),
        ("False", False),
        ("false", False),
        ("0", False),
    ],
    ids=[
        "yes_title",
        "yes_lower",
        "yes_upper",
        "true_title",
        "true_lower",
        "one",
        "no_title",
        "no_lower",
        "false_title",
        "false_lower",
        "zero",
    ],
)
def test_parse_facilitator_valid_values(value, expected):
    assert parse_facilitator(value) is expected


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "value",
    ["maybe", "", "2", "Y", "N", "  ", "unknown"],
    ids=["maybe", "empty", "two", "y", "n", "whitespace", "unknown"],
)
def test_parse_facilitator_rejects_invalid_values(value):
    with pytest.raises(MeetingParseError):
        parse_facilitator(value)


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "csv_content,expected_names",
    [
        (
            "Name,Email,Duration_Minutes,Facilitator\n"
            "Alice,alice@example.com,45,Yes\n"
            "Bob,bob@example.com,30,No\n",
            {"Alice", "Bob"},
        ),
        (
            "Name,Email,Duration_Minutes,Facilitator\n"
            "Alice,alice@example.com,0,No\n",
            {"Alice"},
        ),
    ],
    ids=["two_students", "zero_duration"],
)
def test_parse_attendance_csv_valid(csv_content, expected_names):
    result = parse_attendance_csv(csv_content)
    assert set(result.keys()) == expected_names
    if "Bob" in expected_names:
        assert result["Alice"].duration_minutes == 45
        assert result["Bob"].duration_minutes == 30
    else:
        assert result["Alice"].duration_minutes == 0


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "csv_content,error_match",
    [
        ("", "empty or missing a header"),
        ("Name,Email\nAlice,alice@example.com", "missing required columns"),
        (
            "Name,Email,Duration_Minutes,Facilitator\n"
            "Alice,alice@example.com,,Yes\n",
            "Duration_Minutes and Facilitator are required",
        ),
        (
            "Name,Email,Duration_Minutes,Facilitator\n"
            "Alice,,45,Yes\n",
            "Email is required",
        ),
        (
            "Name,Email,Duration_Minutes,Facilitator\n"
            "Alice,alice@example.com,abc,Yes\n",
            "invalid Duration_Minutes",
        ),
        (
            "Name,Email,Duration_Minutes,Facilitator\n"
            "Alice,alice@example.com,-5,Yes\n",
            "must be non-negative",
        ),
        (
            "Name,Email,Duration_Minutes,Facilitator\n"
            "Alice,alice@example.com,45,maybe\n",
            "Invalid Facilitator value",
        ),
        (
            "Name,Email,Duration_Minutes,Facilitator\n"
            ",,\n",
            "no student rows",
        ),
    ],
    ids=[
        "empty_file",
        "missing_columns",
        "missing_duration",
        "missing_email",
        "invalid_duration",
        "negative_duration",
        "invalid_facilitator",
        "no_data_rows",
    ],
)
def test_parse_attendance_csv_rejects_invalid(csv_content, error_match):
    with pytest.raises(MeetingParseError, match=error_match):
        parse_attendance_csv(csv_content)


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "content,expected_emails",
    [
        (
            "Name,Email\nJane Doe,jane@example.com\nJohn Smith,john@example.com\n",
            {"jane@example.com", "john@example.com"},
        ),
        (
            "Jane Doe, jane@example.com\nJohn Smith <john@example.com>\n",
            {"jane@example.com", "john@example.com"},
        ),
        (
            "Jane Doe, jane@example.com\nJane Doe, jane@example.com\n",
            {"jane@example.com"},
        ),
        (
            "Name,Email\nJane Doe,jane@example.com\n,\nBob,bob@example.com\n",
            {"jane@example.com", "bob@example.com"},
        ),
        (
            "Jane Doe, jane@example.com\n\nBob Smith, bob@example.com\n",
            {"jane@example.com", "bob@example.com"},
        ),
    ],
    ids=[
        "csv_format",
        "plain_text_format",
        "dedupe_by_email",
        "csv_skips_blank_row",
        "plain_text_skips_blank_line",
    ],
)
def test_parse_member_list_valid(content, expected_emails):
    members = parse_member_list(content)
    assert {m.email for m in members} == expected_emails


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "content,error_match",
    [
        ("", "empty"),
        ("Name only\n", "no email address found"),
        ("No Email Here\n", "no email address found"),
        ("Alice, alice@invalid\n", "no email address found"),
        (
            "Name,Email\nAlice,not-an-email\n",
            "valid email are required",
        ),
        (
            "Name,Email Address\nJane,jane@example.com\n",
            "Name' and 'Email' columns",
        ),
        ("Name,Email\n\n", "no members"),
        ("jane@example.com\n", "member name is missing"),
    ],
    ids=[
        "empty",
        "missing_email_line",
        "no_at_sign",
        "malformed_email",
        "invalid_csv_email",
        "missing_email_column",
        "empty_csv_body",
        "email_only_no_name",
    ],
)
def test_parse_member_list_rejects_invalid(content, error_match):
    with pytest.raises(MeetingParseError, match=error_match):
        parse_member_list(content)


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "content,expected",
    [
        ("[00:00] Alice: Hello\n[01:30] Bob: Hi there\n", {"Alice": 1, "Bob": 1}),
        ("[12:05] Alice: One\n[12:05] Alice: Two\n", {"Alice": 2}),
        ("", {}),
        (
            "[00:00] Alice: Hello\n\n[01:00] Bob: Hi\n",
            {"Alice": 1, "Bob": 1},
        ),
    ],
    ids=[
        "valid_two_speakers",
        "duplicate_speaker",
        "empty_content",
        "skips_blank_line",
    ],
)
def test_parse_transcript_or_chat_valid(content, expected):
    assert parse_transcript_or_chat(content, label="Transcript") == expected


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "content,error_match",
    [
        ("Alice: missing timestamp\n", "does not match"),
        ("10:00 Alice: no brackets\n", "does not match"),
        ("[10:00] No colon here\n", "does not match"),
        ("[00:00]  : hello\n", "speaker name is empty"),
    ],
    ids=["missing_timestamp", "no_brackets", "missing_colon", "empty_speaker_name"],
)
def test_parse_transcript_or_chat_rejects_invalid(content, error_match):
    with pytest.raises(MeetingParseError, match=error_match):
        parse_transcript_or_chat(content, label="Transcript")


@pytest.mark.edge_case
@pytest.mark.parametrize(
    "content,expected_minutes",
    [
        ("[00:00] A: hi\n[01:30] B: bye\n", 90),
        ("no timestamps here\n", 0),
        ("[02:15] A: last line\n", 135),
    ],
    ids=["two_lines", "no_timestamps", "single_line"],
)
def test_last_timestamp_minutes(content, expected_minutes):
    assert last_timestamp_minutes(content) == expected_minutes


@pytest.mark.edge_case
def test_unique_display_names_merges_sources():
    attendance = {"Alice": AttendanceRecord(duration_minutes=30, was_facilitator=True)}
    speaking = {"Bob": 5}
    chat = {"Alice": 2, "Carol": 1}

    names = unique_display_names(attendance, speaking, chat)

    assert names == {"Alice", "Bob", "Carol"}
