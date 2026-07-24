from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models import TrainingCollectionStatus
from app.services.training_engine import (
    _normalize_email,
    collect_training_data,
    get_training_collection_detail,
    list_training_collections,
)


def test_normalize_email_strips_and_lowercases():
    assert _normalize_email("  Alice@Example.COM  ") == "alice@example.com"


def test_normalize_email_returns_none_for_blank_values():
    assert _normalize_email(None) is None
    assert _normalize_email("   ") is None


@pytest.mark.asyncio
async def test_collect_training_data_requires_at_least_one_source():
    identity_csv = MagicMock()
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await collect_training_data(
            collector=MagicMock(id="u1"),
            identity_csv=identity_csv,
            github_urls=[],
            google_doc_urls=[],
            meetings_meta=[],
            meeting_files=[],
            db=db,
        )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_collect_training_data_rejects_meeting_file_mismatch():
    identity_csv = MagicMock()
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await collect_training_data(
            collector=MagicMock(id="u1"),
            identity_csv=identity_csv,
            github_urls=[],
            google_doc_urls=[],
            meetings_meta=[MagicMock(), MagicMock()],
            meeting_files=[(MagicMock(), MagicMock(), None)],
            db=db,
        )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_get_training_collection_detail_raises_when_missing():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await get_training_collection_detail("missing", db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_training_collection_detail_returns_members_and_rows():
    member = SimpleNamespace(
        display_name="Alice",
        github_email="alice@github.com",
        google_docs_email="alice@school.edu",
        google_meet_email="alice@school.edu",
    )
    collection = SimpleNamespace(
        id="c1",
        project_group_id="g1",
        dataset_group_id="1",
        created_by_user_id="u1",
        status=TrainingCollectionStatus.COMPLETED,
        error_message=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        members=[member],
    )
    dataset_row = SimpleNamespace(
        id="row-1",
        student_id="1",
        group_id="1",
        code_commits=0.5,
        code_share=0.5,
        review_participation=0.0,
        attendance_ratio=0.0,
        speaking_participation_ratio=0.0,
        chat_participation_ratio=0.0,
        docs_contribution_share=0.0,
        comment_activity=0.0,
        benchmark_score=0.5,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=collection)
    rows_result = MagicMock()
    rows_result.all.return_value = [dataset_row]
    db.scalars = AsyncMock(return_value=rows_result)

    detail = await get_training_collection_detail("c1", db)

    assert detail.id == "c1"
    assert detail.members[0].name == "Alice"
    assert len(detail.dataset_rows) == 1


@pytest.mark.asyncio
async def test_list_training_collections_returns_rows():
    collection = MagicMock(id="c1")
    result = MagicMock()
    result.all.return_value = [collection]
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=result)

    collections = await list_training_collections(db)

    assert collections == [collection]
