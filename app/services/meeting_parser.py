import csv
import io
import re
from dataclasses import dataclass, field

_LINE_PATTERN = re.compile(r"^\[\d{2}:\d{2}\] .+?: .+$")
_FACILITATOR_TRUE = {"yes", "true", "1"}
_FACILITATOR_FALSE = {"no", "false", "0"}


class MeetingParseError(Exception):
    pass


@dataclass
class AttendanceRecord:
    duration_minutes: int
    was_facilitator: bool


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

    required = {"Student_ID", "Duration_Minutes", "Facilitator"}
    missing = required - set(reader.fieldnames)
    if missing:
        raise MeetingParseError(
            f"Attendance CSV missing required columns: {', '.join(sorted(missing))}"
        )

    attendance: dict[str, AttendanceRecord] = {}
    for row_num, row in enumerate(reader, start=2):
        student_id = (row.get("Student_ID") or "").strip()
        if not student_id:
            continue

        duration_raw = (row.get("Duration_Minutes") or "").strip()
        facilitator_raw = (row.get("Facilitator") or "").strip()
        if not duration_raw or not facilitator_raw:
            raise MeetingParseError(
                f"Attendance CSV row {row_num}: Duration_Minutes and Facilitator are required."
            )

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

        attendance[student_id] = AttendanceRecord(
            duration_minutes=duration,
            was_facilitator=parse_facilitator(facilitator_raw),
        )

    if not attendance:
        raise MeetingParseError("Attendance CSV contains no student rows.")

    return attendance


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
