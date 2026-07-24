import csv
import io
from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi import HTTPException

from app.models import CollabTrackDataset
from app.services.dataset import REQUIRED_COLUMNS, parse_dataset_csv, serialize_dataset_csv


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


def test_parse_dataset_csv_reads_valid_rows():
    content = (
        ",".join(REQUIRED_COLUMNS) + "\n"
        "1,8,0.28,0.33,0.0,0.0,0.0,0.0,0.4,0.0,0.347\n"
        "2,8,0.10,0.20,0.0,0.0,0.0,0.0,0.1,0.0,0.150\n"
    )
    records, skipped = parse_dataset_csv(io.BytesIO(content.encode()))

    assert skipped == 0
    assert len(records) == 2
    assert records[0].student_id == "1"
    assert records[1].benchmark_score == 0.15


def test_parse_dataset_csv_skips_invalid_rows():
    content = (
        ",".join(REQUIRED_COLUMNS) + "\n"
        "1,8,0.28,0.33,0.0,0.0,0.0,0.0,0.4,0.0,0.347\n"
        "bad,row,values\n"
    )
    records, skipped = parse_dataset_csv(io.BytesIO(content.encode()))

    assert len(records) == 1
    assert skipped == 1


@pytest.mark.parametrize(
    "content,match",
    [
        ("", "empty or missing a header"),
        ("student_id\n1", "missing required columns"),
    ],
)
def test_parse_dataset_csv_rejects_invalid_files(content, match):
    with pytest.raises(HTTPException, match=match):
        parse_dataset_csv(io.BytesIO(content.encode()))


def test_parse_dataset_csv_rejects_when_no_valid_rows():
    content = ",".join(REQUIRED_COLUMNS) + "\n" + ",,,,,,,,,,\n"
    with pytest.raises(HTTPException, match="No valid data rows"):
        parse_dataset_csv(io.BytesIO(content.encode()))


def test_parse_dataset_csv_skips_rows_with_missing_required_values():
    content = (
        ",".join(REQUIRED_COLUMNS) + "\n"
        "1,8,0.28,0.33,0.0,0.0,0.0,0.0,0.4,0.0,0.347\n"
        "2,8,,0.33,0.0,0.0,0.0,0.0,0.4,0.0,0.347\n"
    )
    records, skipped = parse_dataset_csv(io.BytesIO(content.encode()))

    assert len(records) == 1
    assert skipped == 1


def test_parse_dataset_csv_rejects_non_utf8_encoding(monkeypatch):
    def _raise_unicode_error(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr("app.services.dataset.io.TextIOWrapper", _raise_unicode_error)

    with pytest.raises(HTTPException, match="UTF-8"):
        parse_dataset_csv(BytesIO(b"\xff\xfe\x00\x00"))


def test_parse_dataset_csv_skips_blank_rows():
    content = (
        ",".join(REQUIRED_COLUMNS) + "\n"
        "1,8,0.28,0.33,0.0,0.0,0.0,0.0,0.4,0.0,0.347\n"
        ",,,,,,,,,,\n"
    )
    records, skipped = parse_dataset_csv(io.BytesIO(content.encode()))

    assert len(records) == 1
    assert skipped == 0


@pytest.mark.asyncio
async def test_allocate_dataset_group_id_increments_numeric_ids():
    from unittest.mock import AsyncMock, MagicMock

    from app.services.dataset import allocate_dataset_group_id

    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = ["1", "4", "not-a-number"]
    db.scalars = AsyncMock(return_value=result)

    group_id = await allocate_dataset_group_id(db)

    assert group_id == "5"
