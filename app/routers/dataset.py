from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.models import CollabTrackDataset
from app.schemas.dataset import CollabTrackDatasetOut, DatasetUploadResult
from app.schemas.response import ApiResponse, success
from app.services.dataset import parse_dataset_csv

router = APIRouter(
    prefix="/collab-track-dataset",
    tags=["dataset"],
)


@router.get(
    "",
    response_model=ApiResponse[list[CollabTrackDatasetOut]],
    summary="List all collab track dataset records",
)
async def list_dataset_records(db: AsyncSession = Depends(get_db)):
    """Return every row stored in the `collab_track_dataset` table."""
    result = await db.scalars(
        select(CollabTrackDataset).order_by(
            CollabTrackDataset.group_id.asc(),
            CollabTrackDataset.student_id.asc(),
        )
    )
    records = [CollabTrackDatasetOut.model_validate(row) for row in result.all()]
    return success(
        data=records,
        message="Dataset records retrieved successfully.",
    )


@router.post(
    "/upload",
    response_model=ApiResponse[DatasetUploadResult],
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CSV file to import dataset records",
    dependencies=[Depends(get_current_admin)],
    responses={
        400: {"description": "Invalid or empty CSV file."},
        403: {"description": "Admin privileges required."},
    },
)
async def upload_dataset_csv(
    file: UploadFile = File(..., description="CSV file matching collab_tracker_dataset format"),
    db: AsyncSession = Depends(get_db),
):
    """Import rows from a CSV file into `collab_track_dataset`.

    Expected columns: `student_id`, `group_id`, `assignment_type`,
    `commit_consistency`, `code_share`, `review_participation`,
    `attendance_ratio`, `speaking_participation_ratio`,
    `chat_participation_ratio`, `docs_contribution_share`,
    `comment_activity`, `benchmark_score`.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must be a .csv file.",
        )

    records, skipped = parse_dataset_csv(file.file)
    db.add_all(records)

    return success(
        data=DatasetUploadResult(
            imported_count=len(records),
            skipped_count=skipped,
        ),
        message="Dataset CSV imported successfully.",
        code=status.HTTP_201_CREATED,
    )
