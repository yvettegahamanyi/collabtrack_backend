import csv
import io
from typing import BinaryIO

from fastapi import HTTPException, status

from app.models import CollabTrackDataset

REQUIRED_COLUMNS = [
    "student_id",
    "group_id",
    "code_commits",
    "code_share",
    "review_participation",
    "attendance_ratio",
    "speaking_participation_ratio",
    "chat_participation_ratio",
    "docs_contribution_share",
    "comment_activity",
    "benchmark_score",
]

FLOAT_COLUMNS = [
    "code_commits",
    "code_share",
    "review_participation",
    "attendance_ratio",
    "speaking_participation_ratio",
    "chat_participation_ratio",
    "docs_contribution_share",
    "comment_activity",
    "benchmark_score",
]


def parse_dataset_csv(file: BinaryIO) -> tuple[list[CollabTrackDataset], int]:
    """Parse a CSV upload into CollabTrackDataset ORM instances."""
    try:
        text = io.TextIOWrapper(file, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file must be UTF-8 encoded.",
        ) from exc

    if reader.fieldnames is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file is empty or missing a header row.",
        )

    normalized_headers = {name.strip().lower(): name for name in reader.fieldnames}
    missing = [col for col in REQUIRED_COLUMNS if col not in normalized_headers]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV is missing required columns: {', '.join(missing)}",
        )

    records: list[CollabTrackDataset] = []
    skipped = 0

    for row_number, row in enumerate(reader, start=2):
        if not any(value and str(value).strip() for value in row.values()):
            continue

        try:
            values: dict[str, object] = {}
            for column in REQUIRED_COLUMNS:
                raw = row.get(normalized_headers[column], "")
                raw = "" if raw is None else str(raw).strip()
                if not raw:
                    raise ValueError(f"{column} is required")

                if column in FLOAT_COLUMNS:
                    values[column] = float(raw)
                else:
                    values[column] = raw

            records.append(CollabTrackDataset(**values))
        except (TypeError, ValueError):
            skipped += 1
            continue

    if not records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid data rows found in the CSV file.",
        )

    return records, skipped
