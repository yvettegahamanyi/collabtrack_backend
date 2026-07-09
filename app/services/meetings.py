from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    EngagementScore,
    GroupMembership,
    MeetingFileType,
    MeetingNameMapping,
    MeetingSession,
    MeetingSessionFile,
    MeetingSessionStatus,
    ProjectGroup,
    User,
)
from app.schemas.meetings import (
    GroupEngagementReport,
    MeetingSessionCreate,
    MeetingSessionOut,
    NameMappingSubmit,
    StudentEngagementOut,
    UnmappedNameOut,
)
from app.services.engagement_calculator import recalculate_group_engagement
from app.services.meeting_processor import process_meeting_session
from app.services.storage import delete_files, sanitize_filename, upload_meeting_file

_FILE_TYPE_LABELS = {
    MeetingFileType.ATTENDANCE: "attendance",
    MeetingFileType.TRANSCRIPT: "transcript",
    MeetingFileType.CHAT: "chat",
}


def serialize_session(session: MeetingSession) -> MeetingSessionOut:
    unmapped = None
    if session.status == MeetingSessionStatus.NEEDS_MAPPING and session.unmapped_names:
        unmapped = [
            UnmappedNameOut(display_name=item["display_name"])
            for item in session.unmapped_names
        ]

    return MeetingSessionOut(
        id=session.id,
        group_id=session.group_id,
        session_label=session.session_label,
        session_date=session.session_date,
        duration_minutes=session.duration_minutes,
        status=session.status.value,
        uploaded_at=session.uploaded_at,
        processed_at=session.processed_at,
        error_message=session.error_message,
        unmapped_names=unmapped,
    )


async def create_meeting_session(
    group: ProjectGroup,
    payload: MeetingSessionCreate,
    user: User,
    db: AsyncSession,
) -> MeetingSession:
    if payload.session_date is not None:
        existing = await db.scalar(
            select(MeetingSession).where(
                MeetingSession.group_id == group.id,
                MeetingSession.session_date == payload.session_date,
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A meeting session already exists for this date.",
            )

    session_label = (payload.session_label or "").strip()
    if not session_label:
        session_count = (
            await db.scalar(
                select(func.count())
                .select_from(MeetingSession)
                .where(MeetingSession.group_id == group.id)
            )
        ) or 0
        session_label = f"Meeting {session_count + 1}"

    session = MeetingSession(
        group_id=group.id,
        session_label=session_label,
        session_date=payload.session_date,
        duration_minutes=payload.duration_minutes,
        status=MeetingSessionStatus.PENDING,
        uploaded_by=user.id,
    )
    db.add(session)
    await db.flush()
    return session


async def list_meeting_sessions(
    group_id: str, db: AsyncSession
) -> list[MeetingSessionOut]:
    sessions = await db.scalars(
        select(MeetingSession)
        .where(MeetingSession.group_id == group_id)
        .order_by(
            MeetingSession.session_date.desc().nulls_last(),
            MeetingSession.uploaded_at.desc().nulls_last(),
        )
    )
    return [serialize_session(session) for session in sessions.all()]


async def get_meeting_session_or_404(
    group_id: str, meeting_id: str, db: AsyncSession
) -> MeetingSession:
    session = await db.scalar(
        select(MeetingSession).where(
            MeetingSession.id == meeting_id,
            MeetingSession.group_id == group_id,
        )
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting session not found.",
        )
    return session


async def _process_and_refresh_session(
    session: MeetingSession, db: AsyncSession
) -> MeetingSession:
    """Commit pending changes, process files inline, return the updated session."""
    await db.commit()
    await process_meeting_session(session.id, session.group_id)
    return await get_meeting_session_or_404(session.group_id, session.id, db)


async def upload_meeting_files(
    session: MeetingSession,
    *,
    transcript_file: UploadFile,
    attendance_file: UploadFile | None = None,
    chat_file: UploadFile | None = None,
    user: User,
    db: AsyncSession,
) -> MeetingSessionOut:
    if session.status not in (
        MeetingSessionStatus.PENDING,
        MeetingSessionStatus.UPLOADED,
        MeetingSessionStatus.NEEDS_MAPPING,
        MeetingSessionStatus.FAILED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot upload files while the session is being processed.",
        )

    uploads: list[tuple[UploadFile, MeetingFileType, str]] = [
        (transcript_file, MeetingFileType.TRANSCRIPT, ".txt"),
    ]
    if attendance_file is not None and (attendance_file.filename or "").strip():
        uploads.append((attendance_file, MeetingFileType.ATTENDANCE, ".csv"))
    if chat_file is not None and (chat_file.filename or "").strip():
        uploads.append((chat_file, MeetingFileType.CHAT, ".txt"))

    for upload, _file_type, expected_ext in uploads:
        filename = upload.filename or ""
        if not filename.lower().endswith(expected_ext):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Expected a {expected_ext} file, got '{filename}'.",
            )

    await _remove_session_files(session, db)

    now = datetime.now(timezone.utc)
    for upload, file_type, expected_ext in uploads:
        try:
            object_key = await upload_meeting_file(
                group_id=session.group_id,
                meeting_id=session.id,
                file_type=_FILE_TYPE_LABELS[file_type],
                upload_file=upload,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        safe_name = sanitize_filename(upload.filename or f"file{expected_ext}")
        db.add(
            MeetingSessionFile(
                meeting_session_id=session.id,
                file_type=file_type,
                storage_path=object_key,
                original_filename=safe_name,
                uploaded_at=now,
            )
        )

    session.status = MeetingSessionStatus.UPLOADED
    session.uploaded_by = user.id
    session.uploaded_at = now
    session.processed_at = None
    session.error_message = None
    session.unmapped_names = None
    db.add(session)
    await db.flush()

    session = await _process_and_refresh_session(session, db)
    return serialize_session(session)


async def submit_name_mappings(
    session: MeetingSession,
    payload: NameMappingSubmit,
    db: AsyncSession,
) -> MeetingSessionOut:
    if session.status != MeetingSessionStatus.NEEDS_MAPPING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Name mappings can only be submitted when status is NEEDS_MAPPING.",
        )

    member_ids = set(
        await db.scalars(
            select(GroupMembership.user_id).where(
                GroupMembership.group_id == session.group_id
            )
        )
    )

    for item in payload.mappings:
        if item.user_id not in member_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"User '{item.user_id}' is not a member of this group.",
            )

        existing = await db.scalar(
            select(MeetingNameMapping).where(
                MeetingNameMapping.group_id == session.group_id,
                MeetingNameMapping.display_name == item.display_name,
            )
        )
        if existing:
            existing.user_id = item.user_id
            db.add(existing)
        else:
            db.add(
                MeetingNameMapping(
                    group_id=session.group_id,
                    display_name=item.display_name,
                    user_id=item.user_id,
                )
            )

    session.status = MeetingSessionStatus.UPLOADED
    session.unmapped_names = None
    db.add(session)
    await db.flush()

    session = await _process_and_refresh_session(session, db)
    return serialize_session(session)


async def delete_meeting_session(
    session: MeetingSession, db: AsyncSession
) -> None:
    group_id = session.group_id
    was_completed = session.status == MeetingSessionStatus.COMPLETED
    storage_keys = await _detach_session_files(session, db)
    await db.delete(session)
    await db.flush()
    if was_completed:
        await recalculate_group_engagement(group_id, db)
    delete_files(storage_keys)


async def get_group_engagement_report(
    group: ProjectGroup, db: AsyncSession
) -> GroupEngagementReport:
    scores = await db.scalars(
        select(EngagementScore)
        .where(EngagementScore.group_id == group.id)
        .options(selectinload(EngagementScore.user))
        .order_by(EngagementScore.user_id.asc())
    )
    score_rows = list(scores.all())

    completed_count = len(
        list(
            await db.scalars(
                select(MeetingSession.id).where(
                    MeetingSession.group_id == group.id,
                    MeetingSession.status == MeetingSessionStatus.COMPLETED,
                )
            )
        )
    )

    last_updated = None
    if score_rows:
        last_updated = max(row.last_updated for row in score_rows)

    return GroupEngagementReport(
        group_id=group.id,
        total_sessions=completed_count,
        last_updated=last_updated,
        engagement_scores=[
            StudentEngagementOut(
                user_id=score.user_id,
                student_name=score.user.name or score.user.email,
                attendance_ratio=score.attendance_ratio,
                speaking_ratio=score.speaking_ratio,
                chat_participation=score.chat_participation,
                meeting_lead_count=score.meeting_lead_count,
                sessions_attended=score.sessions_attended,
                total_sessions=score.total_sessions,
            )
            for score in score_rows
        ],
    )


async def get_engagement_scores_by_user(
    group_id: str, db: AsyncSession
) -> dict[str, EngagementScore]:
    scores = await db.scalars(
        select(EngagementScore).where(EngagementScore.group_id == group_id)
    )
    return {score.user_id: score for score in scores.all()}


async def _detach_session_files(
    session: MeetingSession, db: AsyncSession
) -> list[str]:
    """Remove file records from the DB and return storage keys for cleanup."""
    files = await db.scalars(
        select(MeetingSessionFile).where(
            MeetingSessionFile.meeting_session_id == session.id
        )
    )
    file_rows = list(files.all())
    storage_keys = [record.storage_path for record in file_rows]

    for record in file_rows:
        await db.delete(record)

    await db.execute(
        delete(MeetingSessionFile).where(
            MeetingSessionFile.meeting_session_id == session.id
        )
    )
    return storage_keys


async def _remove_session_files(session: MeetingSession, db: AsyncSession) -> None:
    storage_keys = await _detach_session_files(session, db)
    delete_files(storage_keys)
