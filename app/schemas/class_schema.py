from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assignment import AssignmentOut


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class ClassOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    instructor_id: str
    created_at: datetime
    assignment_count: int = 0


class ClassDetailOut(ClassOut):
    assignments: list[AssignmentOut] = []