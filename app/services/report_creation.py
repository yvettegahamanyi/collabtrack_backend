import json
import logging
from dataclasses import dataclass
from io import BytesIO

from fastapi import HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.database import AsyncSessionLocal
from app.models import (
    Assignment,
    GroupMemberRole,
    GroupMembership,
    ProjectGroup,
    ReportStatus,
    User,
)
from app.schemas.meetings import MeetingSessionCreate
from app.schemas.report import CreateReportOut, ReportMemberInput
from app.services.assignments import allocate_group_number
from app.services.contribution_report import check_and_finalize_report
from app.services.integrations import parse_github_repo_url, parse_google_doc_url
from app.services.meeting_parser import MemberRow
from app.services.meetings import create_meeting_session, upload_meeting_files
from app.services.participation import link_github_repo, link_google_doc, sync_group_participation
from app.services.report_provisioning import provision_members_from_attendance

logger = logging.getLogger(__name__)


@dataclass
class MeetingFilePayload:
    transcript: bytes
    transcript_filename: str
    chat: bytes | None = None
    chat_filename: str | None = None


def _bytes_upload(content: bytes, filename: str) -> UploadFile:
    return StarletteUploadFile(filename=filename, file=BytesIO(content))


async def bootstrap_assignment_report(
    *,
    assignment_id: str,
    instructor: User,
    members: list[MemberRow],
    github_urls: list[str],
    google_doc_urls: list[str],
    db: AsyncSession,
) -> tuple[ProjectGroup, CreateReportOut]:
    if not github_urls and not google_doc_urls:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Add at least one GitHub repository URL or Google Doc URL.",
        )

    if not members:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Add at least one group member.",
        )
    member_rows = members

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

    return group, CreateReportOut(
        group_id=group.id,
        group_name=group_name,
        group_number=group_number,
        assignment_id=assignment_id,
        report_status=ReportStatus.PROCESSING,
        members_provisioned=members_added,
        meetings_created=0,
    )


async def process_assignment_report_meetings(
    *,
    group_id: str,
    assignment_id: str,
    instructor_id: str,
    meetings: list[MeetingFilePayload],
) -> None:
    async with AsyncSessionLocal() as db:
        group = await db.get(ProjectGroup, group_id)
        instructor = await db.get(User, instructor_id)
        assignment = await db.get(Assignment, assignment_id)
        if group is None or instructor is None or assignment is None:
            return

        meetings_created = 0
        try:
            for payload in meetings:
                session = await create_meeting_session(
                    group,
                    MeetingSessionCreate(),
                    instructor,
                    db,
                )
                chat_upload = None
                if payload.chat is not None:
                    chat_upload = _bytes_upload(
                        payload.chat, payload.chat_filename or "chat.txt"
                    )
                await upload_meeting_files(
                    session,
                    transcript_file=_bytes_upload(
                        payload.transcript, payload.transcript_filename
                    ),
                    chat_file=chat_upload,
                    user=instructor,
                    db=db,
                )
                meetings_created += 1

            try:
                await sync_group_participation(group, db)
            except HTTPException:
                pass

            await db.commit()
            await check_and_finalize_report(group.id)
        except Exception:
            logger.exception("Assignment report processing failed for group %s", group_id)
            await db.rollback()
            group = await db.get(ProjectGroup, group_id)
            if group is not None:
                group.report_status = ReportStatus.FAILED
                db.add(group)
                await db.commit()


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


def parse_members_payload(raw: str | None) -> list[MemberRow]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON for members list.",
        ) from exc
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Members list must be a JSON array.",
        )

    rows: dict[str, MemberRow] = {}
    for index, item in enumerate(parsed, start=1):
        try:
            member = ReportMemberInput.model_validate(item)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Member {index}: a name and a valid email are required.",
            ) from exc
        email = member.email.lower()
        rows.setdefault(
            email, MemberRow(name=member.name.strip(), email=email)
        )
    return list(rows.values())
