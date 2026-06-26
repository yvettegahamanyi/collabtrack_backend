from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_instructor
from app.models import CourseClass, User
from app.schemas.class_schema import ClassCreate, ClassDetailOut, ClassOut, ClassUpdate
from app.schemas.response import ApiResponse, success
from app.services.classes import (
    list_assignments_for_class,
    list_classes_for_instructor,
    require_class_owner,
    serialize_class,
    serialize_class_detail,
)

router = APIRouter(prefix="/classes", tags=["classes"])


@router.post(
    "",
    response_model=ApiResponse[ClassOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_class(
    payload: ClassCreate,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    course_class = CourseClass(
        name=payload.name.strip(),
        description=payload.description,
        instructor_id=current_user.id,
    )
    db.add(course_class)
    await db.flush()
    return success(
        data=serialize_class(course_class),
        message="Class created successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.get("", response_model=ApiResponse[list[ClassOut]])
async def list_classes(
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    classes = await list_classes_for_instructor(current_user, db)
    return success(data=classes, message="Classes retrieved successfully.")


@router.get("/{class_id}", response_model=ApiResponse[ClassDetailOut])
async def get_class(
    class_id: str,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    course_class = await require_class_owner(class_id, current_user, db)
    assignments = await list_assignments_for_class(class_id, db)
    return success(
        data=serialize_class_detail(course_class, assignments),
        message="Class retrieved successfully.",
    )


@router.put("/{class_id}", response_model=ApiResponse[ClassOut])
async def update_class(
    class_id: str,
    payload: ClassUpdate,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    course_class = await require_class_owner(class_id, current_user, db)
    if payload.name is not None:
        course_class.name = payload.name.strip()
    if payload.description is not None:
        course_class.description = payload.description
    db.add(course_class)
    await db.flush()
    from app.services.classes import count_assignments

    count = await count_assignments(class_id, db)
    return success(
        data=serialize_class(course_class, assignment_count=count),
        message="Class updated successfully.",
    )


@router.delete("/{class_id}", response_model=ApiResponse[None])
async def delete_class(
    class_id: str,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    course_class = await require_class_owner(class_id, current_user, db)
    await db.delete(course_class)
    return success(data=None, message="Class deleted successfully.")
