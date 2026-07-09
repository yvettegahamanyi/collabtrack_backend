import csv
import io
import re
from dataclasses import dataclass, field

_LINE_PATTERN = re.compile(r"^\[\d{2}:\d{2}\] .+?: .+$")
_TIMESTAMP_PREFIX = re.compile(r"^\[(\d{2}):(\d{2})\]")
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_FACILITATOR_TRUE = {"yes", "true", "1"}
_FACILITATOR_FALSE = {"no", "false", "0"}


class MeetingParseError(Exception):
    pass


@dataclass
class AttendanceRecord:
    duration_minutes: int
    was_facilitator: bool
    email: str | None = None


@dataclass
class ParsedMeetingData:
    attendance: dict[str, AttendanceRecord] = field(default_factory=dict)
    speaking: dict[str, int] = field(default_factory=dict)
    chat: dict[str, int] = field(default_factory=dict)


def parse_facilitator(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _FACILITATOR_TRUE:
        return True
    if normalized in _FACILITATOR_FALSE:
        return False
    raise MeetingParseError(
        f"Invalid Facilitator value '{value}'. Use Yes/No, True/False, or 1/0."
    )


def parse_attendance_csv(content: str) -> dict[str, AttendanceRecord]:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise MeetingParseError("Attendance CSV is empty or missing a header row.")

    required = {"Name", "Email", "Duration_Minutes", "Facilitator"}
    missing = required - set(reader.fieldnames)
    if missing:
        raise MeetingParseError(
            f"Attendance CSV missing required columns: {', '.join(sorted(missing))}"
        )

    attendance: dict[str, AttendanceRecord] = {}
    for row_num, row in enumerate(reader, start=2):
        name = (row.get("Name") or "").strip()
        if not name:
            continue

        duration_raw = (row.get("Duration_Minutes") or "").strip()
        facilitator_raw = (row.get("Facilitator") or "").strip()
        email_raw = (row.get("Email") or "").strip()
        if not duration_raw or not facilitator_raw:
            raise MeetingParseError(
                f"Attendance CSV row {row_num}: Duration_Minutes and Facilitator are required."
            )
        if not email_raw:
            raise MeetingParseError(
                f"Attendance CSV row {row_num}: Email is required."
            )

        email = email_raw.lower()

        try:
            duration = int(duration_raw)
        except ValueError as exc:
            raise MeetingParseError(
                f"Attendance CSV row {row_num}: invalid Duration_Minutes '{duration_raw}'."
            ) from exc

        if duration < 0:
            raise MeetingParseError(
                f"Attendance CSV row {row_num}: Duration_Minutes must be non-negative."
            )

        attendance[name] = AttendanceRecord(
            duration_minutes=duration,
            was_facilitator=parse_facilitator(facilitator_raw),
            email=email,
        )

    if not attendance:
        raise MeetingParseError("Attendance CSV contains no student rows.")

    return attendance


@dataclass
class MemberRow:
    name: str
    email: str


def _parse_member_csv(content: str) -> list[MemberRow]:
    reader = csv.DictReader(io.StringIO(content))
    fieldnames = reader.fieldnames or []
    lookup = {name.strip().lower(): name for name in fieldnames}
    name_col = lookup.get("name")
    email_col = lookup.get("email")
    if name_col is None or email_col is None:
        raise MeetingParseError(
            "Member list CSV must have 'Name' and 'Email' columns."
        )

    rows: list[MemberRow] = []
    for row_num, row in enumerate(reader, start=2):
        name = (row.get(name_col) or "").strip()
        email = (row.get(email_col) or "").strip().lower()
        if not name and not email:
            continue
        if not name or not _EMAIL_PATTERN.fullmatch(email):
            raise MeetingParseError(
                f"Member list row {row_num}: a name and a valid email are required."
            )
        rows.append(MemberRow(name=name, email=email))
    return rows


def _parse_member_lines(content: str) -> list[MemberRow]:
    rows: list[MemberRow] = []
    for line_num, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = _EMAIL_PATTERN.search(line)
        if match is None:
            raise MeetingParseError(
                f"Member list line {line_num}: no email address found."
            )
        email = match.group(0).lower()
        name = (line[: match.start()] + line[match.end():]).strip(" ,;<>\t-")
        if not name:
            raise MeetingParseError(
                f"Member list line {line_num}: member name is missing."
            )
        rows.append(MemberRow(name=name, email=email))
    return rows


def parse_member_list(content: str) -> list[MemberRow]:
    """Parse a group member list with names and emails.

    Accepts a CSV with Name/Email columns, or plain text lines such as
    'Jane Doe, jane@example.com' or 'Jane Doe <jane@example.com>'.
    """
    stripped = content.strip()
    if not stripped:
        raise MeetingParseError("Member list file is empty.")

    first_line = stripped.splitlines()[0].lower()
    if "name" in first_line and "email" in first_line and "@" not in first_line:
        rows = _parse_member_csv(content)
    else:
        rows = _parse_member_lines(content)

    deduped: dict[str, MemberRow] = {}
    for row in rows:
        deduped.setdefault(row.email, row)
    if not deduped:
        raise MeetingParseError("Member list contains no members.")
    return list(deduped.values())


def last_timestamp_minutes(content: str) -> int:
    """Return the minute of the last [HH:MM] timestamp in a transcript/chat."""
    latest = 0
    for raw_line in content.splitlines():
        match = _TIMESTAMP_PREFIX.match(raw_line.strip())
        if match is None:
            continue
        minutes = int(match.group(1)) * 60 + int(match.group(2))
        latest = max(latest, minutes)
    return latest


def parse_transcript_or_chat(content: str, *, label: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line_num, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if not _LINE_PATTERN.match(line):
            raise MeetingParseError(
                f"{label} line {line_num} does not match "
                "'[HH:MM] Name: message' format."
            )

        name_part = line.split("]", 1)[1].strip()
        name, _message = name_part.split(":", 1)
        name = name.strip()
        if not name:
            raise MeetingParseError(f"{label} line {line_num}: speaker name is empty.")

        counts[name] = counts.get(name, 0) + 1

    return counts


def unique_display_names(
    attendance: dict[str, AttendanceRecord],
    speaking: dict[str, int],
    chat: dict[str, int],
) -> set[str]:
    names: set[str] = set()
    names.update(attendance.keys())
    names.update(speaking.keys())
    names.update(chat.keys())
    return names
