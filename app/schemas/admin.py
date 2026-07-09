from pydantic import BaseModel, ConfigDict, Field


class AdminStatsOut(BaseModel):
    """Platform-wide counts for the admin dashboard."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_count": 42,
                "active_user_count": 39,
                "report_count": 18,
                "class_count": 6,
                "assignment_count": 12,
            }
        }
    )

    user_count: int = Field(description="Total registered users.")
    active_user_count: int = Field(description="Users who can currently log in.")
    report_count: int = Field(description="Assignment group reports across the platform.")
    class_count: int = Field(description="Total classes created.")
    assignment_count: int = Field(description="Total assignments created.")
