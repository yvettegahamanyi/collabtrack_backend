from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StudentClusterOut(BaseModel):
    cluster_id: int
    cluster_key: str
    cluster_label: str
    composite_score: float = 0.0
    active_platforms: list[str] = Field(default_factory=list)


class LLMRationaleOut(BaseModel):
    reasoning: str = ""
    top_area: str | None = None
    flags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    group_observations: str = ""
    model_version: str = ""


class ParticipationScoreOut(BaseModel):
    user_id: str
    name: str | None = None
    predicted_score: float = Field(ge=0.0, le=1.0)
    contributor_tier: str
    features: dict[str, float] = Field(default_factory=dict)
    generated_at: datetime
    student_cluster: StudentClusterOut | None = None
    llm_rationale: LLMRationaleOut | None = None


class ParticipationScoresSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id: str
    generated_at: datetime
    scores: list[ParticipationScoreOut]
    warnings: list[str] = Field(default_factory=list)
