from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CollabTrackDatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    student_id: str
    group_id: str
    code_commits: float
    code_share: float
    review_participation: float
    attendance_ratio: float
    speaking_participation_ratio: float
    chat_participation_ratio: float
    docs_contribution_share: float
    comment_activity: float
    benchmark_score: float
    created_at: datetime


class DatasetUploadResult(BaseModel):
    imported_count: int = Field(examples=[100])
    skipped_count: int = Field(
        default=0,
        description="Rows skipped due to validation errors.",
        examples=[0],
    )
