import csv
import io
from datetime import datetime, timezone

from app.models import CollabTrackDataset
from app.services.dataset import REQUIRED_COLUMNS, serialize_dataset_csv


def test_serialize_dataset_csv_writes_required_columns():
    record = CollabTrackDataset(
        id="row-1",
        student_id="1",
        group_id="8",
        code_commits=0.28,
        code_share=0.33,
        review_participation=0.0,
        attendance_ratio=0.0,
        speaking_participation_ratio=0.0,
        chat_participation_ratio=0.0,
        docs_contribution_share=0.4,
        comment_activity=0.0,
        benchmark_score=0.347,
        created_at=datetime.now(timezone.utc),
    )

    content = serialize_dataset_csv([record])
    reader = csv.DictReader(io.StringIO(content))

    assert reader.fieldnames == REQUIRED_COLUMNS
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["student_id"] == "1"
    assert rows[0]["group_id"] == "8"
    assert float(rows[0]["benchmark_score"]) == 0.347


def test_serialize_dataset_csv_empty_dataset_has_header_only():
    content = serialize_dataset_csv([])
    lines = content.strip().splitlines()

    assert len(lines) == 1
    assert lines[0] == ",".join(REQUIRED_COLUMNS)
