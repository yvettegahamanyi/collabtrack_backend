from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import GroupMemberRole
from app.services.meeting_parser import AttendanceRecord
from app.services.meeting_processor import (
    _resolve_names,
    _training_meet_email_map,
)


def _membership(user_id: str, name: str, email: str):
    return SimpleNamespace(
        user_id=user_id,
        role=GroupMemberRole.STUDENT,
        user=SimpleNamespace(id=user_id, name=name, email=email),
    )


@pytest.mark.asyncio
async def test_training_meet_email_map_returns_empty_without_collection():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    mapping = await _training_meet_email_map("g1", db)

    assert mapping == {}


@pytest.mark.asyncio
async def test_training_meet_email_map_builds_email_lookup():
    collection = SimpleNamespace(id="c1")
    member = SimpleNamespace(
        user_id="u1",
        google_meet_email="Alice@School.edu",
    )
    scalars_result = MagicMock()
    scalars_result.all.return_value = [member]
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=collection)
    db.scalars = AsyncMock(return_value=scalars_result)

    mapping = await _training_meet_email_map("g1", db)

    assert mapping == {"alice@school.edu": "u1"}


@pytest.mark.asyncio
async def test_resolve_names_matches_attendance_email():
    membership = _membership("u1", "Alice", "alice@school.edu")
    memberships_result = MagicMock()
    memberships_result.all.return_value = [membership]
    mappings_result = MagicMock()
    mappings_result.all.return_value = []

    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[mappings_result, memberships_result])
    db.scalar = AsyncMock(return_value=None)

    attendance = {
        "Alice": AttendanceRecord(
            duration_minutes=45,
            was_facilitator=False,
            email="alice@school.edu",
        )
    }

    resolved, unmapped = await _resolve_names(
        "g1",
        {"Alice"},
        db,
        attendance=attendance,
    )

    assert resolved == {"Alice": "u1"}
    assert unmapped == set()


@pytest.mark.asyncio
async def test_resolve_names_uses_manual_mapping():
    mapping_row = SimpleNamespace(display_name="A. Example", user_id="u1")
    mappings_result = MagicMock()
    mappings_result.all.return_value = [mapping_row]
    memberships_result = MagicMock()
    memberships_result.all.return_value = []

    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[mappings_result, memberships_result])
    db.scalar = AsyncMock(return_value=None)

    resolved, unmapped = await _resolve_names(
        "g1",
        {"A. Example", "Unknown Speaker"},
        db,
        attendance={"A. Example": AttendanceRecord(duration_minutes=30, was_facilitator=False)},
    )

    assert resolved["A. Example"] == "u1"
    assert "Unknown Speaker" in unmapped


@pytest.mark.asyncio
async def test_resolve_names_aliases_transcript_name_to_attendance():
    membership = _membership("u1", "Alice Smith", "alice@school.edu")
    memberships_result = MagicMock()
    memberships_result.all.return_value = [membership]
    mappings_result = MagicMock()
    mappings_result.all.return_value = []

    db = AsyncMock()
    db.scalars = AsyncMock(side_effect=[mappings_result, memberships_result])
    db.scalar = AsyncMock(return_value=None)

    attendance = {
        "Alice Smith": AttendanceRecord(
            duration_minutes=45,
            was_facilitator=False,
            email="alice@school.edu",
        )
    }

    resolved, unmapped = await _resolve_names(
        "g1",
        {"Alice Smith", "alice smith"},
        db,
        attendance=attendance,
    )

    assert resolved["Alice Smith"] == "u1"
    assert resolved["alice smith"] == "u1"
    assert unmapped == set()
