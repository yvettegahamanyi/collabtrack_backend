from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models import AccountStatus, GroupMemberRole, ReportStatus, ServiceType
from app.services.groups import (
    get_group_or_404,
    get_invitation_by_token,
    require_membership,
    require_owner,
    require_owner_or_instructor,
    serialize_group_detail,
)


def _user(user_id: str, *, role: GroupMemberRole = GroupMemberRole.STUDENT):
    return SimpleNamespace(id=user_id, role=role, name=user_id, email=f"{user_id}@example.com")


def _group(owner_id: str = "u1"):
    return SimpleNamespace(
        id="g1",
        group_name="Team Alpha",
        description="Capstone",
        assignment_status=ServiceType.ACTIVE,
        git_weight=0.33,
        doc_weight=0.33,
        transcript_weight=0.34,
        owner_id=owner_id,
        assignment_id=None,
        group_number=1,
        report_status=ReportStatus.DRAFT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _membership(
    user_id: str,
    *,
    role: GroupMemberRole = GroupMemberRole.STUDENT,
    joined_at: datetime | None = None,
):
    if joined_at is None:
        joined_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        user_id=user_id,
        user=SimpleNamespace(
            id=user_id,
            name=user_id.title(),
            email=f"{user_id}@example.com",
            account_status=AccountStatus.ACTIVE,
        ),
        role=role,
        joined_at=joined_at,
    )


def test_serialize_group_detail_includes_members():
    group = _group()
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = datetime(2026, 1, 2, tzinfo=timezone.utc)
    members = [
        _membership("u2", joined_at=later),
        _membership("u1", joined_at=earlier),
    ]

    detail = serialize_group_detail(group, members)

    assert detail.id == "g1"
    assert len(detail.members) == 2
    assert detail.members[0].user_id == "u1"
    assert detail.members[0].is_owner is True
    assert detail.members[1].is_owner is False


@pytest.mark.asyncio
async def test_get_group_or_404_raises_when_missing():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await get_group_or_404("missing", db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_group_or_404_returns_group():
    group = _group()
    db = AsyncMock()
    db.get = AsyncMock(return_value=group)

    result = await get_group_or_404("g1", db)

    assert result is group


@pytest.mark.asyncio
async def test_require_owner_allows_owner():
    await require_owner(_group("u1"), _user("u1"))


@pytest.mark.asyncio
async def test_require_owner_rejects_non_owner():
    with pytest.raises(HTTPException) as exc:
        await require_owner(_group("u1"), _user("u2"))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_membership_raises_for_non_member():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await require_membership("g1", _user("u2"), db)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_membership_returns_membership():
    membership = _membership("u2")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=membership)

    result = await require_membership("g1", _user("u2"), db)

    assert result is membership


@pytest.mark.asyncio
async def test_require_owner_or_instructor_allows_owner():
    membership = _membership("u1")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=membership)

    result = await require_owner_or_instructor(_group("u1"), _user("u1"), db)

    assert result is membership


@pytest.mark.asyncio
async def test_require_owner_or_instructor_allows_instructor_member():
    membership = _membership("u2", role=GroupMemberRole.INSTRUCTOR)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=membership)

    result = await require_owner_or_instructor(_group("u1"), _user("u2"), db)

    assert result is membership


@pytest.mark.asyncio
async def test_require_owner_or_instructor_rejects_student_non_owner():
    membership = _membership("u2", role=GroupMemberRole.STUDENT)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=membership)

    with pytest.raises(HTTPException) as exc:
        await require_owner_or_instructor(_group("u1"), _user("u2"), db)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_invitation_by_token_returns_none_when_missing(monkeypatch):
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.core.security.hash_invite_token",
        lambda token: f"hash-{token}",
    )

    result = await get_invitation_by_token("secret-token", db)

    assert result is None
