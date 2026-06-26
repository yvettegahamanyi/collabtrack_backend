from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Assignment, CourseClass, ProjectGroup, RoleType, User
from app.schemas.assignment import AssignmentDetailOut, AssignmentOut
from app.schemas.class_schema import ClassDetailOut, ClassOut


async def get_class_or_404(class_id: str, db: AsyncSession) -> CourseClass:
    course_class = await db.get(CourseClass, class_id)
    if course_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found.",
        )
    return course_class


async def require_class_owner(
    class_id: str, user: User, db: AsyncSession
) -> CourseClass:
    if user.role != RoleType.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can manage classes.",
        )
    course_class = await get_class_or_404(class_id, db)
    if course_class.instructor_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this class.",
        )
    return course_class


async def get_assignment_or_404(assignment_id: str, db: AsyncSession) -> Assignment:
    assignment = await db.scalar(
        select(Assignment)
        .where(Assignment.id == assignment_id)
        .options(selectinload(Assignment.course_class))
    )
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found.",
        )
    return assignment


async def require_assignment_owner(
    assignment_id: str, user: User, db: AsyncSession
) -> Assignment:
    assignment = await get_assignment_or_404(assignment_id, db)
    if user.role != RoleType.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can manage assignments.",
        )
    if assignment.course_class.instructor_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this assignment.",
        )
    return assignment


def serialize_class(course_class: CourseClass, *, assignment_count: int = 0) -> ClassOut:
    return ClassOut(
        id=course_class.id,
        name=course_class.name,
        description=course_class.description,
        instructor_id=course_class.instructor_id,
        created_at=course_class.created_at,
        assignment_count=assignment_count,
    )


def serialize_class_detail(
    course_class: CourseClass, assignments: list[AssignmentOut]
) -> ClassDetailOut:
    return ClassDetailOut(
        **serialize_class(course_class, assignment_count=len(assignments)).model_dump(),
        assignments=assignments,
    )


def serialize_assignment(
    assignment: Assignment, *, report_count: int = 0
) -> AssignmentOut:
    return AssignmentOut(
        id=assignment.id,
        class_id=assignment.class_id,
        title=assignment.title,
        description=assignment.description,
        supervisor_email=assignment.supervisor_email,
        status=assignment.status,
        created_at=assignment.created_at,
        report_count=report_count,
    )


async def count_assignments(class_id: str, db: AsyncSession) -> int:
    return await db.scalar(
        select(func.count()).select_from(Assignment).where(Assignment.class_id == class_id)
    ) or 0


async def count_reports(assignment_id: str, db: AsyncSession) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(ProjectGroup)
        .where(ProjectGroup.assignment_id == assignment_id)
    ) or 0


async def list_classes_for_instructor(user: User, db: AsyncSession) -> list[ClassOut]:
    classes = await db.scalars(
        select(CourseClass)
        .where(CourseClass.instructor_id == user.id)
        .order_by(CourseClass.created_at.desc())
    )
    result: list[ClassOut] = []
    for course_class in classes.all():
        count = await count_assignments(course_class.id, db)
        result.append(serialize_class(course_class, assignment_count=count))
    return result


async def list_assignments_for_class(
    class_id: str, db: AsyncSession
) -> list[AssignmentOut]:
    assignments = await db.scalars(
        select(Assignment)
        .where(Assignment.class_id == class_id)
        .order_by(Assignment.created_at.desc())
    )
    result: list[AssignmentOut] = []
    for assignment in assignments.all():
        count = await count_reports(assignment.id, db)
        result.append(serialize_assignment(assignment, report_count=count))
    return result
