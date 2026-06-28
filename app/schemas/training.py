from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import TrainingCollectionStatus
from app.schemas.dataset import CollabTrackDatasetOut


class IdentityMemberPreview(BaseModel):
    name: str
    github_email: str | None = None
    google_docs_email: str | None = None
    google_meet_email: str | None = None


class TrainingCollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_group_id: str
    dataset_group_id: str
    created_by_user_id: str
    status: TrainingCollectionStatus
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class TrainingCollectionDetailOut(TrainingCollectionOut):
    members: list[IdentityMemberPreview] = Field(default_factory=list)
    dataset_rows: list[CollabTrackDatasetOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TrainingCollectionListOut(BaseModel):
    collections: list[TrainingCollectionOut]
