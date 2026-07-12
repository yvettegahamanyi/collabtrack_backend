from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.classes import serialize_assignment, serialize_class, serialize_class_detail


def test_serialize_class_includes_assignment_count():
    course_class = SimpleNamespace(
        id="c1",
        name="CS Capstone",
        description="Team projects",
        instructor_id="inst-1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    result = serialize_class(course_class, assignment_count=3)

    assert result.id == "c1"
    assert result.assignment_count == 3


def test_serialize_assignment_includes_report_count():
    assignment = SimpleNamespace(
        id="a1",
        class_id="c1",
        title="Final report",
        description="Submit weekly",
        supervisor_email="supervisor@example.com",
        status="ACTIVE",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    result = serialize_assignment(assignment, report_count=4)

    assert result.title == "Final report"
    assert result.report_count == 4


def test_serialize_class_detail_embeds_assignments():
    course_class = SimpleNamespace(
        id="c1",
        name="CS Capstone",
        description="Team projects",
        instructor_id="inst-1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assignment = serialize_assignment(
        SimpleNamespace(
            id="a1",
            class_id="c1",
            title="Final report",
            description="Submit weekly",
            supervisor_email="supervisor@example.com",
            status="ACTIVE",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )

    detail = serialize_class_detail(course_class, [assignment])

    assert detail.assignment_count == 1
    assert detail.assignments[0].id == "a1"
