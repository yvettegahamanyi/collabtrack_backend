import json
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Assignment,
    CourseClass,
    GroupMemberRole,
    GroupMembership,
    MeetingSession,
    MeetingSessionStatus,
    ProjectGroup,
    ReportStatus,
    User,
)
from app.schemas.meetings import MeetingSessionCreate
from app.schemas.report import CreateReportOut, MeetingInputMeta
from app.services.assignments import allocate_group_number
from app.services.contribution_report import aggregate_and_save_report, check_and_finalize_report
from app.services.email import send_supervisor_report_notification
from app.services.integrations import parse_github_repo_url, parse_google_doc_url
from app.services.meeting_parser import MeetingParseError, parse_attendance_members
from app.services.meetings import create_meeting_session, upload_meeting_files
from app.services.participation import link_github_repo, link_google_doc, sync_group_participation
from app.services.report_provisioning import provision_members_from_attendance


async def create_assignment_report(
    *,
    assignment_id: str,
    instructor: User,
    attendance_file: UploadFile,
    github_urls: list[str],
    google_doc_urls: list[str],
    meetings_meta: list[MeetingInputMeta],
    meeting_files: list[tuple[UploadFile, UploadFile, UploadFile]],
    db: AsyncSession,
) -> CreateReportOut:
    if not github_urls and not google_doc_urls:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Add at least one GitHub repository URL or Google Doc URL.",
        )
    if len(meetings_meta) < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one meeting is required.",
        )
    if len(meeting_files) != len(meetings_meta):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Each meeting must include attendance, transcript, and chat files.",
        )

    content = (await attendance_file.read()).decode("utf-8")
    try:
        member_rows = parse_attendance_members(content)
    except MeetingParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    group_number = await allocate_group_number(assignment_id, db)
    group_name = f"Group {group_number}"

    group = ProjectGroup(
        group_name=group_name,
        description=None,
        owner_id=instructor.id,
        assignment_id=assignment_id,
        group_number=group_number,
        report_status=ReportStatus.PROCESSING,
    )
    db.add(group)
    await db.flush()

    db.add(
        GroupMembership(
            group_id=group.id,
            user_id=instructor.id,
            role=GroupMemberRole.INSTRUCTOR,
        )
    )

    members_added = await provision_members_from_attendance(
        db,
        group_id=group.id,
        instructor_id=instructor.id,
        rows=member_rows,
    )

    for url in github_urls:
        owner, repo = parse_github_repo_url(url.strip())
        await link_github_repo(group, url.strip(), owner, repo, db)

    for url in google_doc_urls:
        file_id = parse_google_doc_url(url.strip())
        await link_google_doc(group, url.strip(), file_id, db)

    await db.commit()
    await db.refresh(group)

    meetings_created = 0
    for meta, (att_file, trans_file, chat_file) in zip(
        meetings_meta, meeting_files, strict=True
    ):
        session = await create_meeting_session(
            group,
            MeetingSessionCreate(
                session_label=meta.session_label,
                session_date=meta.session_date,
                duration_minutes=meta.duration_minutes,
            ),
            instructor,
            db,
        )
        await upload_meeting_files(
            session,
            attendance_file=att_file,
            transcript_file=trans_file,
            chat_file=chat_file,
            user=instructor,
            db=db,
        )
        meetings_created += 1

    try:
        await sync_group_participation(group, db)
    except HTTPException:
        pass

    assignment = await db.scalar(
        select(Assignment)
        .where(Assignment.id == assignment_id)
        .options(
            selectinload(Assignment.course_class).selectinload(CourseClass.instructor)
        )
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found.")

    sessions = list(
        (await db.scalars(select(MeetingSession).where(MeetingSession.group_id == group.id))).all()
    )
    all_completed = sessions and all(
        session.status == MeetingSessionStatus.COMPLETED for session in sessions
    )
    any_failed = any(session.status == MeetingSessionStatus.FAILED for session in sessions)
    any_needs_mapping = any(
        session.status == MeetingSessionStatus.NEEDS_MAPPING for session in sessions
    )

    await db.refresh(group)

    if any_failed:
        group.report_status = ReportStatus.FAILED
        db.add(group)
        await db.commit()
    elif any_needs_mapping:
        group.report_status = ReportStatus.PROCESSING
        db.add(group)
        await db.commit()
    elif all_completed:
        report = await aggregate_and_save_report(group, assignment, db)
        supervisor_email = (
            assignment.supervisor_email or assignment.course_class.instructor.email
        )
        if supervisor_email:
            try:
                await send_supervisor_report_notification(
                    to_email=supervisor_email,
                    assignment_title=assignment.title,
                    group_name=group_name,
                    assignment_id=assignment.id,
                    group_id=group.id,
                )
                report.notification_sent_at = datetime.now(timezone.utc)
                db.add(report)
            except Exception:
                pass
        await db.commit()
    else:
        await check_and_finalize_report(group.id)
        await db.refresh(group)

    return CreateReportOut(
        group_id=group.id,
        group_name=group_name,
        group_number=group_number,
        assignment_id=assignment_id,
        report_status=group.report_status or ReportStatus.PROCESSING,
        members_provisioned=members_added,
        meetings_created=meetings_created,
    )


def parse_url_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON for URL list.",
        ) from exc
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="URL list must be a JSON array.",
        )
    return [str(item).strip() for item in parsed if str(item).strip()]


def parse_meetings_meta(raw: str) -> list[MeetingInputMeta]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON for meetings metadata.",
        ) from exc
    if not isinstance(parsed, list) or not parsed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Meetings metadata must be a non-empty JSON array.",
        )
    return [MeetingInputMeta.model_validate(item) for item in parsed]
