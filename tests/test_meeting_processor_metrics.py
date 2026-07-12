from app.services.meeting_parser import AttendanceRecord
from app.services.meeting_processor import _build_raw_metrics


def test_build_raw_metrics_aggregates_attendance_speaking_and_chat():
    attendance = {
        "Alice": AttendanceRecord(
            duration_minutes=40, was_facilitator=True, email="alice@example.com"
        ),
        "Bob": AttendanceRecord(
            duration_minutes=30, was_facilitator=False, email="bob@example.com"
        ),
    }
    speaking = {"Alice": 3, "Bob": 1}
    chat = {"Alice": 2, "Bob": 4}
    name_to_user_id = {"Alice": "u1", "Bob": "u2"}

    metrics = _build_raw_metrics(
        attendance=attendance,
        speaking=speaking,
        chat=chat,
        name_to_user_id=name_to_user_id,
        session_duration_minutes=45,
    )

    assert metrics["u1"]["duration_minutes"] == 40
    assert metrics["u1"]["was_facilitator"] is True
    assert metrics["u1"]["speaking_turns"] == 3
    assert metrics["u1"]["chat_messages"] == 2
    assert metrics["u2"]["speaking_turns"] == 1
    assert metrics["u2"]["chat_messages"] == 4


def test_build_raw_metrics_without_attendance_uses_session_duration():
    metrics = _build_raw_metrics(
        attendance={},
        speaking={"Alice": 2},
        chat={"Alice": 1},
        name_to_user_id={"Alice": "u1"},
        session_duration_minutes=60,
    )

    assert metrics["u1"]["duration_minutes"] == 60
    assert metrics["u1"]["speaking_turns"] == 2
    assert metrics["u1"]["chat_messages"] == 1
