from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models import RoleType
from app.services.classes import (
    get_assignment_or_404,
    get_class_or_404,
    require_assignment_owner,
    require_class_owner,
)


def _instructor(user_id: str = "inst-1") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=RoleType.INSTRUCTOR)


def _student(user_id: str = "stu-1") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=RoleType.STUDENT)


@pytest.mark.asyncio
async def test_get_class_or_404_returns_class():
    course_class = SimpleNamespace(id="c1", instructor_id="inst-1")
    db = AsyncMock()
    db.get = AsyncMock(return_value=course_class)

    result = await get_class_or_404("c1", db)

    assert result is course_class


@pytest.mark.asyncio
async def test_get_class_or_404_raises_when_missing():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await get_class_or_404("missing", db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_class_owner_rejects_non_instructor():
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await require_class_owner("c1", _student(), db)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_class_owner_rejects_wrong_instructor():
    course_class = SimpleNamespace(id="c1", instructor_id="other")
    db = AsyncMock()
    db.get = AsyncMock(return_value=course_class)

    with pytest.raises(HTTPException) as exc:
        await require_class_owner("c1", _instructor(), db)

    assert exc.value.status_code == 403
    assert "own" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_require_class_owner_returns_class_for_owner():
    course_class = SimpleNamespace(id="c1", instructor_id="inst-1")
    db = AsyncMock()
    db.get = AsyncMock(return_value=course_class)

    result = await require_class_owner("c1", _instructor(), db)

    assert result is course_class


@pytest.mark.asyncio
async def test_get_assignment_or_404_raises_when_missing():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await get_assignment_or_404("missing", db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_assignment_owner_rejects_student():
    assignment = SimpleNamespace(
        id="a1",
        course_class=SimpleNamespace(instructor_id="inst-1"),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=assignment)

    with pytest.raises(HTTPException) as exc:
        await require_assignment_owner("a1", _student(), db)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_assignment_owner_rejects_non_owner_instructor():
    assignment = SimpleNamespace(
        id="a1",
        course_class=SimpleNamespace(instructor_id="other"),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=assignment)

    with pytest.raises(HTTPException) as exc:
        await require_assignment_owner("a1", _instructor(), db)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_assignment_owner_returns_assignment_for_owner():
    assignment = SimpleNamespace(
        id="a1",
        class_id="c1",
        title="Capstone",
        description="Build something",
        supervisor_email="super@example.com",
        status="ACTIVE",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        course_class=SimpleNamespace(instructor_id="inst-1"),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=assignment)

    result = await require_assignment_owner("a1", _instructor(), db)

    assert result is assignment


@pytest.mark.asyncio
async def test_count_assignments_returns_zero_when_scalar_is_none():
    from app.services.classes import count_assignments

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    count = await count_assignments("c1", db)

    assert count == 0


@pytest.mark.asyncio
async def test_list_classes_for_instructor_serializes_with_counts():
    from app.services.classes import list_classes_for_instructor

    course_class = SimpleNamespace(
        id="c1",
        name="Capstone",
        description="Projects",
        instructor_id="inst-1",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    classes_result = MagicMock()
    classes_result.all.return_value = [course_class]
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=classes_result)
    db.scalar = AsyncMock(return_value=2)

    classes = await list_classes_for_instructor(_instructor(), db)

    assert len(classes) == 1
    assert classes[0].assignment_count == 2

