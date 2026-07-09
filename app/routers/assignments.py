from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.database import get_db
from app.dependencies import get_current_instructor
from app.models import Assignment, ReportStatus, User
from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentDetailOut,
    AssignmentOut,
    AssignmentUpdate,
)
from app.schemas.report import (
    AssignmentReportDetailOut,
    AssignmentReportOut,
    CreateReportOut,
    MemberPreview,
    MembersPreviewOut,
)
from app.schemas.response import ApiResponse, success
from app.services.assignments import serialize_assignment_reports
from app.services.classes import (
    count_reports,
    list_assignments_for_class,
    require_assignment_owner,
    require_class_owner,
    serialize_assignment,
)
from app.services.contribution_report import (
    attempt_complete_report_delivery,
    resend_supervisor_notification,
)
from app.services.groups import get_group_or_404
from app.services.meeting_parser import MeetingParseError, parse_member_list
from app.services.participation import get_contributions
from app.services.report_creation import (
    MeetingFilePayload,
    bootstrap_assignment_report,
    parse_members_payload,
    parse_url_list,
    process_assignment_report_meetings,
)

router = APIRouter(tags=["assignments"])


@router.post(
    "/classes/{class_id}/assignments",
    response_model=ApiResponse[AssignmentOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_assignment(
    class_id: str,
    payload: AssignmentCreate,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    await require_class_owner(class_id, current_user, db)
    assignment = Assignment(
        class_id=class_id,
        title=payload.title.strip(),
        description=payload.description,
        supervisor_email=(
            payload.supervisor_email.lower() if payload.supervisor_email else None
        ),
        status=payload.status,
    )
    db.add(assignment)
    await db.flush()
    return success(
        data=serialize_assignment(assignment),
        message="Assignment created successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.get(
    "/classes/{class_id}/assignments",
    response_model=ApiResponse[list[AssignmentOut]],
)
async def list_class_assignments(
    class_id: str,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    await require_class_owner(class_id, current_user, db)
    assignments = await list_assignments_for_class(class_id, db)
    return success(data=assignments, message="Assignments retrieved successfully.")


@router.get(
    "/assignments/{assignment_id}",
    response_model=ApiResponse[AssignmentDetailOut],
)
async def get_assignment(
    assignment_id: str,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    assignment = await require_assignment_owner(assignment_id, current_user, db)
    reports = await serialize_assignment_reports(assignment_id, db)
    return success(
        data=AssignmentDetailOut(
            **serialize_assignment(
                assignment, report_count=len(reports)
            ).model_dump(),
            class_name=assignment.course_class.name,
            reports=reports,
        ),
        message="Assignment retrieved successfully.",
    )


@router.put(
    "/assignments/{assignment_id}",
    response_model=ApiResponse[AssignmentOut],
)
async def update_assignment(
    assignment_id: str,
    payload: AssignmentUpdate,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    assignment = await require_assignment_owner(assignment_id, current_user, db)
    if payload.title is not None:
        assignment.title = payload.title.strip()
    if payload.description is not None:
        assignment.description = payload.description
    if payload.supervisor_email is not None:
        assignment.supervisor_email = payload.supervisor_email.lower()
    if payload.status is not None:
        assignment.status = payload.status
    db.add(assignment)
    await db.flush()
    count = await count_reports(assignment_id, db)
    return success(
        data=serialize_assignment(assignment, report_count=count),
        message="Assignment updated successfully.",
    )


@router.delete(
    "/assignments/{assignment_id}",
    response_model=ApiResponse[None],
)
async def delete_assignment(
    assignment_id: str,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    assignment = await require_assignment_owner(assignment_id, current_user, db)
    await db.delete(assignment)
    return success(data=None, message="Assignment deleted successfully.")


@router.post(
    "/assignments/{assignment_id}/reports/preview-members",
    response_model=ApiResponse[MembersPreviewOut],
)
async def preview_members(
    assignment_id: str,
    members_file: UploadFile = File(...),
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    await require_assignment_owner(assignment_id, current_user, db)
    content = (await members_file.read()).decode("utf-8")
    try:
        rows = parse_member_list(content)
    except MeetingParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return success(
        data=MembersPreviewOut(
            members=[
                MemberPreview(name=row.name, email=row.email) for row in rows
            ]
        ),
        message="Member list parsed successfully.",
    )


@router.post(
    "/assignments/{assignment_id}/reports",
    response_model=ApiResponse[CreateReportOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_report(
    assignment_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    await require_assignment_owner(assignment_id, current_user, db)

    form = await request.form()
    members_raw = form.get("members")
    members = parse_members_payload(
        members_raw if isinstance(members_raw, str) else None
    )
    if not members:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Add at least one group member.",
        )

    github_raw = form.get("github_urls")
    google_raw = form.get("google_doc_urls")
    github_list = parse_url_list(
        github_raw if isinstance(github_raw, str) else None
    )
    google_list = parse_url_list(
        google_raw if isinstance(google_raw, str) else None
    )

    meeting_payloads: list[MeetingFilePayload] = []
    index = 0
    while True:
        trans = form.get(f"meeting_{index}_transcript")
        chat = form.get(f"meeting_{index}_chat")
        if not isinstance(trans, StarletteUploadFile):
            if isinstance(chat, StarletteUploadFile):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Meeting {index + 1} requires a transcript file.",
                )
            break
        chat_bytes = None
        chat_filename = None
        if isinstance(chat, StarletteUploadFile):
            chat_bytes = await chat.read()
            chat_filename = chat.filename or f"meeting_{index}_chat.txt"
        meeting_payloads.append(
            MeetingFilePayload(
                transcript=await trans.read(),
                transcript_filename=trans.filename or f"meeting_{index}_transcript.txt",
                chat=chat_bytes,
                chat_filename=chat_filename,
            )
        )
        index += 1

    group, result = await bootstrap_assignment_report(
        assignment_id=assignment_id,
        instructor=current_user,
        members=members,
        github_urls=github_list,
        google_doc_urls=google_list,
        db=db,
    )

    background_tasks.add_task(
        process_assignment_report_meetings,
        group_id=group.id,
        assignment_id=assignment_id,
        instructor_id=current_user.id,
        meetings=meeting_payloads,
    )

    return success(
        data=result,
        message="Report created successfully. Processing in background.",
        code=status.HTTP_201_CREATED,
    )


@router.get(
    "/assignments/{assignment_id}/reports",
    response_model=ApiResponse[list[AssignmentReportOut]],
)
async def list_reports(
    assignment_id: str,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    await require_assignment_owner(assignment_id, current_user, db)
    reports = await serialize_assignment_reports(assignment_id, db)
    return success(data=reports, message="Reports retrieved successfully.")


@router.get(
    "/assignments/{assignment_id}/reports/{group_id}",
    response_model=ApiResponse[AssignmentReportDetailOut],
)
async def get_report(
    assignment_id: str,
    group_id: str,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    assignment = await require_assignment_owner(assignment_id, current_user, db)
    group = await get_group_or_404(group_id, db)
    if group.assignment_id != assignment_id:
        raise HTTPException(status_code=404, detail="Report not found in this assignment.")

    reports = await serialize_assignment_reports(assignment_id, db)
    report_summary = next((item for item in reports if item.group_id == group_id), None)
    if report_summary is None:
        raise HTTPException(status_code=404, detail="Report not found.")

    contributions = None
    if group.report_status and group.report_status.value == "READY":
        await attempt_complete_report_delivery(group, assignment, db)
        await db.refresh(group)
        contributions = await get_contributions(group, db)
    elif group.report_status and group.report_status.value == "PROCESSING":
        contributions = await get_contributions(group, db)

    return success(
        data=AssignmentReportDetailOut(
            **report_summary.model_dump(),
            contributions=contributions,
        ),
        message="Report retrieved successfully.",
    )


@router.post(
    "/assignments/{assignment_id}/reports/{group_id}/notify",
    response_model=ApiResponse[None],
)
async def notify_supervisor(
    assignment_id: str,
    group_id: str,
    current_user: User = Depends(get_current_instructor),
    db: AsyncSession = Depends(get_db),
):
    assignment = await require_assignment_owner(assignment_id, current_user, db)
    group = await get_group_or_404(group_id, db)
    if group.assignment_id != assignment_id:
        raise HTTPException(status_code=404, detail="Report not found in this assignment.")

    await resend_supervisor_notification(group, assignment, db)
    await db.commit()
    return success(data=None, message="Instructor notification sent.")
