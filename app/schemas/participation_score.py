from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ParticipationScoreOut(BaseModel):
    user_id: str
    name: str | None = None
    predicted_score: float = Field(ge=0.0, le=1.0)
    contributor_tier: str
    features: dict[str, float] = Field(default_factory=dict)
    generated_at: datetime


class ParticipationScoresSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id: str
    generated_at: datetime
    scores: list[ParticipationScoreOut]
    warnings: list[str] = Field(default_factory=list)
