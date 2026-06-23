from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import (
    GroupMemberRole,
    GroupMembership,
    MeetingFileType,
    MeetingNameMapping,
    MeetingRawMetric,
    MeetingSession,
    MeetingSessionStatus,
)
from app.services.engagement_calculator import recalculate_group_engagement
from app.services.meeting_parser import (
    AttendanceRecord,
    MeetingParseError,
    parse_attendance_csv,
    parse_transcript_or_chat,
    unique_display_names,
)
from app.services.storage import download_file


async def process_meeting_session(meeting_id: str, group_id: str) -> None:
    async with AsyncSessionLocal() as db:
        session = await db.scalar(
            select(MeetingSession)
            .where(
                MeetingSession.id == meeting_id,
                MeetingSession.group_id == group_id,
            )
            .options(selectinload(MeetingSession.files))
        )
        if session is None:
            return

        try:
            session.status = MeetingSessionStatus.PROCESSING
            session.error_message = None
            session.unmapped_names = None
            db.add(session)
            await db.commit()

            await _process_session(session, group_id, db)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            failed = await db.scalar(
                select(MeetingSession).where(MeetingSession.id == meeting_id)
            )
            if failed is not None:
                failed.status = MeetingSessionStatus.FAILED
                failed.error_message = str(exc)
                db.add(failed)
                await db.commit()


async def _process_session(
    session: MeetingSession, group_id: str, db: AsyncSession
) -> None:
    files_by_type = {record.file_type: record for record in session.files}
    required = (
        MeetingFileType.ATTENDANCE,
        MeetingFileType.TRANSCRIPT,
        MeetingFileType.CHAT,
    )
    missing = [file_type for file_type in required if file_type not in files_by_type]
    if missing:
        session.status = MeetingSessionStatus.FAILED
        session.error_message = "All three meeting files must be uploaded before processing."
        db.add(session)
        return

    try:
        attendance_content = _read_file(
            files_by_type[MeetingFileType.ATTENDANCE].storage_path
        )
        transcript_content = _read_file(
            files_by_type[MeetingFileType.TRANSCRIPT].storage_path
        )
        chat_content = _read_file(
            files_by_type[MeetingFileType.CHAT].storage_path
        )

        attendance = parse_attendance_csv(attendance_content)
        speaking = parse_transcript_or_chat(transcript_content, label="Transcript")
        chat = parse_transcript_or_chat(chat_content, label="Chat")
    except MeetingParseError as exc:
        session.status = MeetingSessionStatus.FAILED
        session.error_message = str(exc)
        db.add(session)
        return
    except Exception as exc:
        session.status = MeetingSessionStatus.FAILED
        session.error_message = f"Could not read uploaded files: {exc}"
        db.add(session)
        return

    all_names = unique_display_names(attendance, speaking, chat)
    name_to_user_id, unmapped = await _resolve_names(
        group_id, all_names, db, attendance=attendance
    )

    if unmapped:
        session.status = MeetingSessionStatus.NEEDS_MAPPING
        session.unmapped_names = [{"display_name": name} for name in sorted(unmapped)]
        db.add(session)
        return

    metrics_by_user = _build_raw_metrics(
        attendance=attendance,
        speaking=speaking,
        chat=chat,
        name_to_user_id=name_to_user_id,
    )

    await db.execute(
        delete(MeetingRawMetric).where(
            MeetingRawMetric.meeting_session_id == session.id
        )
    )
    for user_id, metric in metrics_by_user.items():
        db.add(
            MeetingRawMetric(
                meeting_session_id=session.id,
                user_id=user_id,
                duration_minutes=metric["duration_minutes"],
                was_facilitator=metric["was_facilitator"],
                speaking_turns=metric["speaking_turns"],
                chat_messages=metric["chat_messages"],
            )
        )

    await recalculate_group_engagement(group_id, db)

    session.status = MeetingSessionStatus.COMPLETED
    session.processed_at = datetime.now(timezone.utc)
    session.error_message = None
    session.unmapped_names = None
    db.add(session)


def _read_file(object_key: str) -> str:
    return download_file(object_key).decode("utf-8")


async def _resolve_names(
    group_id: str,
    display_names: set[str],
    db: AsyncSession,
    *,
    attendance: dict[str, AttendanceRecord] | None = None,
) -> tuple[dict[str, str], set[str]]:
    mappings = await db.scalars(
        select(MeetingNameMapping).where(MeetingNameMapping.group_id == group_id)
    )
    mapping_by_name = {row.display_name: row.user_id for row in mappings.all()}

    memberships = await db.scalars(
        select(GroupMembership)
        .where(
            GroupMembership.group_id == group_id,
            GroupMembership.role == GroupMemberRole.STUDENT,
        )
        .options(selectinload(GroupMembership.user))
    )
    user_id_by_lower_name: dict[str, str] = {}
    user_id_by_email: dict[str, str] = {}
    for membership in memberships.all():
        user = membership.user
        user_id_by_email[user.email.lower()] = user.id
        if user.name:
            user_id_by_lower_name[user.name.strip().lower()] = user.id

    attendance = attendance or {}
    resolved: dict[str, str] = {}
    unmapped: set[str] = set()

    for display_name in display_names:
        if display_name in mapping_by_name:
            resolved[display_name] = mapping_by_name[display_name]
            continue

        attendance_row = attendance.get(display_name)
        if attendance_row and attendance_row.email:
            user_id = user_id_by_email.get(attendance_row.email)
            if user_id:
                resolved[display_name] = user_id
                continue

        user_id = user_id_by_lower_name.get(display_name.strip().lower())
        if user_id:
            resolved[display_name] = user_id
            continue

        unmapped.add(display_name)

    return resolved, unmapped


def _build_raw_metrics(
    *,
    attendance: dict,
    speaking: dict[str, int],
    chat: dict[str, int],
    name_to_user_id: dict[str, str],
) -> dict[str, dict]:
    metrics: dict[str, dict] = {}

    def ensure_user(user_id: str) -> dict:
        if user_id not in metrics:
            metrics[user_id] = {
                "duration_minutes": 0,
                "was_facilitator": False,
                "speaking_turns": 0,
                "chat_messages": 0,
            }
        return metrics[user_id]

    for display_name, record in attendance.items():
        user_id = name_to_user_id[display_name]
        entry = ensure_user(user_id)
        entry["duration_minutes"] = max(
            entry["duration_minutes"], record.duration_minutes
        )
        entry["was_facilitator"] = entry["was_facilitator"] or record.was_facilitator

    for display_name, turns in speaking.items():
        user_id = name_to_user_id[display_name]
        ensure_user(user_id)["speaking_turns"] += turns

    for display_name, messages in chat.items():
        user_id = name_to_user_id[display_name]
        ensure_user(user_id)["chat_messages"] += messages

    return metrics
