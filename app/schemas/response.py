from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Standard API envelope: { data, message, code }."""

    data: T | None = None
    message: str = Field(examples=["Operation completed successfully."])
    code: int = Field(examples=[200])


def success(
    *,
    data: T | None = None,
    message: str,
    code: int = 200,
) -> ApiResponse[T]:
    return ApiResponse(data=data, message=message, code=code)
