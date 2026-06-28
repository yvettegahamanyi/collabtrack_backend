import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

load_dotenv()

FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "https://collabtrackfrontend-production.up.railway.app",
)

from app.core.security import generate_invite_token
from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    GroupGithubRepo,
    GroupGoogleDoc,
    GroupInvitation,
    GroupMemberRole,
    GroupMembership,
    ProjectGroup,
    RoleType,
    User,
)
from app.schemas.group import (
    AddGroupMemberRequest,
    GroupCreate,
    GroupDetailOut,
    GroupOut,
    GroupUpdate,
    InviteCreate,
    InviteOut,
    MemberOut,
)
from app.schemas.response import ApiResponse, success
from app.schemas.integration import (
    DocumentLinkIn,
    DocumentOut,
    RepoLinkIn,
    RepoOut,
)
from app.schemas.participation import ContributionsOut, MemberParticipationOut, SyncOut
from app.schemas.participation_score import (
    ParticipationScoreOut,
    ParticipationScoresSummaryOut,
)
from app.services.group_members import (
    add_member_if_missing,
    require_instructor_can_manage_group,
)
from app.services.groups import (
    get_group_members,
    get_group_or_404,
    get_membership,
    require_membership,
    require_owner,
    require_owner_or_instructor,
    serialize_group_detail,
    serialize_members,
)
from app.services.integrations import parse_github_repo_url, parse_google_doc_url
from app.services.participation import (
    get_contributions,
    get_member_participation,
    link_github_repo,
    link_google_doc,
    sync_group_participation,
)
from app.services.participation_scoring import (
    generate_participation_scores,
    get_member_participation_score,
    get_participation_scores_for_group,
)
from app.schemas.meetings import (
    GroupEngagementReport,
    MeetingSessionCreate,
    MeetingSessionOut,
    NameMappingSubmit,
)
from app.services.meetings import (
    create_meeting_session,
    delete_meeting_session,
    get_group_engagement_report,
    get_meeting_session_or_404,
    list_meeting_sessions,
    serialize_session,
    submit_name_mappings,
    upload_meeting_files,
)
from app.services.user_provisioning import get_or_create_student

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post(
    "",
    response_model=ApiResponse[GroupOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project group",
    responses={403: {"description": "Only students or instructors can create groups."}},
)
async def create_group(
    payload: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a group. Students become the group owner; instructors use class/assignment reports."""
    if current_user.role == RoleType.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Instructors should create reports through classes and assignments.",
        )
    if current_user.role != RoleType.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can create standalone groups.",
        )

    membership_role = GroupMemberRole.STUDENT

    group = ProjectGroup(
        group_name=payload.group_name,
        description=payload.description,
        assignment_status=payload.assignment_status,
        owner_id=current_user.id,
    )
    db.add(group)
    await db.flush()

    membership = GroupMembership(
        group_id=group.id,
        user_id=current_user.id,
        role=membership_role,
    )
    db.add(membership)
    return success(
        data=GroupOut.model_validate(group),
        message="Group created successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.get(
    "",
    response_model=ApiResponse[list[GroupDetailOut]],
    summary="List my groups",
)
async def list_my_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all groups the current user belongs to, including members."""
    result = await db.scalars(
        select(ProjectGroup)
        .join(GroupMembership, GroupMembership.group_id == ProjectGroup.id)
        .where(GroupMembership.user_id == current_user.id)
        .options(
            selectinload(ProjectGroup.memberships).selectinload(GroupMembership.user)
        )
        .order_by(ProjectGroup.created_at.desc())
    )
    groups = [
        serialize_group_detail(group, group.memberships)
        for group in result.all()
    ]
    return success(
        data=groups,
        message="Groups retrieved successfully.",
    )


@router.get(
    "/{group_id}",
    response_model=ApiResponse[GroupDetailOut],
    summary="Get group details",
    responses={403: {"description": "Not a member of this group."}},
)
async def get_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    memberships = await get_group_members(group, db)
    return success(
        data=serialize_group_detail(group, memberships),
        message="Group retrieved successfully.",
    )


@router.put(
    "/{group_id}",
    response_model=ApiResponse[GroupOut],
    summary="Update a group",
    responses={403: {"description": "Only the group owner can update."}},
)
async def update_group(
    group_id: str,
    payload: GroupUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    db.add(group)
    return success(
        data=GroupOut.model_validate(group),
        message="Group updated successfully.",
    )


@router.delete(
    "/{group_id}",
    response_model=ApiResponse[None],
    summary="Delete a group",
    responses={403: {"description": "Only the group owner can delete."}},
)
async def delete_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a group and all related memberships, assets, and reports."""
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)
    await db.delete(group)
    return success(
        message="Group deleted successfully.",
    )


@router.post(
    "/{group_id}/invite",
    response_model=ApiResponse[InviteOut],
    status_code=status.HTTP_201_CREATED,
    summary="Generate a shareable invite link",
    responses={
        403: {"description": "Only owner or instructor can invite."},
    },
)
async def create_invite(
    group_id: str,
    payload: InviteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)

    raw_token, token_hash = generate_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=payload.expires_in_hours
    )
    invitation = GroupInvitation(
        group_id=group.id,
        token_hash=token_hash,
        role=payload.role,
        expires_at=expires_at,
        created_by_id=current_user.id,
    )
    db.add(invitation)

    invite_url = f"{FRONTEND_URL.rstrip('/')}/invite/{raw_token}"
    return success(
        data=InviteOut(
            token=raw_token,
            invite_url=invite_url,
            role=payload.role,
            expires_at=expires_at,
            group_id=group.id,
        ),
        message="Invite link created successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.get(
    "/{group_id}/members",
    response_model=ApiResponse[list[MemberOut]],
    summary="List group members",
    responses={403: {"description": "Not a member of this group."}},
)
async def list_members(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)

    memberships = await get_group_members(group, db)
    return success(
        data=serialize_members(group, memberships),
        message="Group members retrieved successfully.",
    )


@router.post(
    "/{group_id}/members",
    response_model=ApiResponse[GroupDetailOut],
    summary="Add a student member to a group",
    responses={
        403: {"description": "Only instructors can add roster members."},
        404: {"description": "Group not found."},
        409: {"description": "Email belongs to a non-student user."},
    },
)
async def add_group_member(
    group_id: str,
    payload: AddGroupMemberRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Provision or attach a student by email and add them to the group roster."""
    group = await get_group_or_404(group_id, db)
    await require_instructor_can_manage_group(group, current_user, db)

    student = await get_or_create_student(
        db,
        email=payload.email,
        name=payload.name,
        instructor_id=current_user.id,
    )

    await add_member_if_missing(
        db,
        group_id=group.id,
        user_id=student.id,
        role=GroupMemberRole.STUDENT,
    )

    memberships = await get_group_members(group, db)
    return success(
        data=serialize_group_detail(group, memberships),
        message="Member added successfully.",
    )


@router.delete(
    "/{group_id}/members/{user_id}",
    response_model=ApiResponse[None],
    summary="Remove a member from a group",
    responses={
        403: {"description": "Not authorized or cannot remove owner."},
        404: {"description": "Member not found in group."},
    },
)
async def remove_member(
    group_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)

    if user_id == group.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot remove the group owner.",
        )

    membership = await get_membership(group_id, user_id, db)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this group.",
        )

    await db.execute(
        delete(GroupMembership).where(GroupMembership.id == membership.id)
    )
    return success(
        message="Member removed successfully.",
    )


@router.get(
    "/{group_id}/repos",
    response_model=ApiResponse[list[RepoOut]],
    summary="List linked GitHub repositories",
)
async def list_repos(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    repos = await db.scalars(
        select(GroupGithubRepo)
        .where(GroupGithubRepo.group_id == group.id)
        .order_by(GroupGithubRepo.created_at.asc())
    )
    return success(
        data=[RepoOut.model_validate(r) for r in repos.all()],
        message="Repositories retrieved successfully.",
    )


@router.post(
    "/{group_id}/repos",
    response_model=ApiResponse[RepoOut],
    status_code=status.HTTP_201_CREATED,
    summary="Link a GitHub repository to a group",
    responses={403: {"description": "Only owner or instructor can link repositories."}},
)
async def add_repo(
    group_id: str,
    payload: RepoLinkIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    owner, repo = parse_github_repo_url(payload.url)
    record = await link_github_repo(group, payload.url, owner, repo, db)
    return success(
        data=RepoOut.model_validate(record),
        message="Repository linked successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.delete(
    "/{group_id}/repos/{repo_id}",
    response_model=ApiResponse[None],
    summary="Unlink a GitHub repository",
    responses={403: {"description": "Only owner or instructor can unlink repositories."}},
)
async def remove_repo(
    group_id: str,
    repo_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    repo = await db.get(GroupGithubRepo, repo_id)
    if repo is None or repo.group_id != group.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found in this group.",
        )
    await db.delete(repo)
    return success(message="Repository unlinked successfully.")


@router.get(
    "/{group_id}/documents",
    response_model=ApiResponse[list[DocumentOut]],
    summary="List linked Google Docs",
)
async def list_documents(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    docs = await db.scalars(
        select(GroupGoogleDoc)
        .where(GroupGoogleDoc.group_id == group.id)
        .order_by(GroupGoogleDoc.created_at.asc())
    )
    return success(
        data=[DocumentOut.model_validate(d) for d in docs.all()],
        message="Documents retrieved successfully.",
    )


@router.post(
    "/{group_id}/documents",
    response_model=ApiResponse[DocumentOut],
    status_code=status.HTTP_201_CREATED,
    summary="Link a Google Doc to a group",
    responses={403: {"description": "Only owner or instructor can link documents."}},
)
async def add_document(
    group_id: str,
    payload: DocumentLinkIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    file_id = parse_google_doc_url(payload.url)
    record = await link_google_doc(group, payload.url, file_id, db)
    return success(
        data=DocumentOut.model_validate(record),
        message="Document linked successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.delete(
    "/{group_id}/documents/{doc_id}",
    response_model=ApiResponse[None],
    summary="Unlink a Google Doc",
    responses={403: {"description": "Only owner or instructor can unlink documents."}},
)
async def remove_document(
    group_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    doc = await db.get(GroupGoogleDoc, doc_id)
    if doc is None or doc.group_id != group.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found in this group.",
        )
    await db.delete(doc)
    return success(message="Document unlinked successfully.")


@router.post(
    "/{group_id}/sync",
    response_model=ApiResponse[SyncOut],
    summary="Sync group participation data from GitHub and Google",
    responses={403: {"description": "Only owner or instructor can sync data."}},
)
async def sync_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    result = await sync_group_participation(group, db)
    return success(
        data=result,
        message="Group participation data synced.",
    )


@router.get(
    "/{group_id}/contributions",
    response_model=ApiResponse[ContributionsOut],
    summary="Get raw contribution metrics for all members",
)
async def get_group_contributions(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    data = await get_contributions(group, db)
    return success(
        data=data,
        message="Contributions retrieved successfully.",
    )


@router.get(
    "/{group_id}/members/{user_id}/participation",
    response_model=ApiResponse[MemberParticipationOut],
    summary="Get participation metrics for one member",
)
async def get_member_participation_endpoint(
    group_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    data = await get_member_participation(group, user_id, db)
    return success(
        data=data,
        message="Participation retrieved successfully.",
    )


@router.post(
    "/{group_id}/meetings",
    response_model=ApiResponse[MeetingSessionOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create a meeting session",
    responses={403: {"description": "Only owner or instructor can create sessions."}},
)
async def create_meeting(
    group_id: str,
    payload: MeetingSessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    session = await create_meeting_session(group, payload, current_user, db)
    return success(
        data=serialize_session(session),
        message="Meeting session created successfully.",
        code=status.HTTP_201_CREATED,
    )


@router.get(
    "/{group_id}/meetings",
    response_model=ApiResponse[list[MeetingSessionOut]],
    summary="List meeting sessions",
)
async def list_meetings(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    data = await list_meeting_sessions(group.id, db)
    return success(
        data=data,
        message="Meeting sessions retrieved successfully.",
    )


@router.get(
    "/{group_id}/meetings/{meeting_id}",
    response_model=ApiResponse[MeetingSessionOut],
    summary="Get meeting session detail",
)
async def get_meeting(
    group_id: str,
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    session = await get_meeting_session_or_404(group.id, meeting_id, db)
    return success(
        data=serialize_session(session),
        message="Meeting session retrieved successfully.",
    )


@router.post(
    "/{group_id}/meetings/{meeting_id}/upload",
    response_model=ApiResponse[MeetingSessionOut],
    summary="Upload meeting session files",
    responses={403: {"description": "Only owner or instructor can upload files."}},
)
async def upload_meeting(
    group_id: str,
    meeting_id: str,
    attendance_file: UploadFile = File(...),
    transcript_file: UploadFile = File(...),
    chat_file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    session = await get_meeting_session_or_404(group.id, meeting_id, db)
    data = await upload_meeting_files(
        session,
        attendance_file=attendance_file,
        transcript_file=transcript_file,
        chat_file=chat_file,
        user=current_user,
        db=db,
    )
    return success(
        data=data,
        message="Meeting files uploaded successfully.",
    )


@router.post(
    "/{group_id}/meetings/{meeting_id}/mapping",
    response_model=ApiResponse[MeetingSessionOut],
    summary="Submit name-to-member mappings",
    responses={403: {"description": "Only owner or instructor can submit mappings."}},
)
async def submit_meeting_mapping(
    group_id: str,
    meeting_id: str,
    payload: NameMappingSubmit,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    session = await get_meeting_session_or_404(group.id, meeting_id, db)
    data = await submit_name_mappings(session, payload, db)
    return success(
        data=data,
        message="Name mappings submitted successfully.",
    )


@router.delete(
    "/{group_id}/meetings/{meeting_id}",
    response_model=ApiResponse[None],
    summary="Delete a meeting session",
    responses={403: {"description": "Only owner or instructor can delete sessions."}},
)
async def delete_meeting(
    group_id: str,
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    session = await get_meeting_session_or_404(group.id, meeting_id, db)
    await delete_meeting_session(session, db)
    return success(message="Meeting session deleted.")


@router.get(
    "/{group_id}/engagement",
    response_model=ApiResponse[GroupEngagementReport],
    summary="Get aggregated meeting engagement report",
)
async def get_group_engagement(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    data = await get_group_engagement_report(group, db)
    return success(
        data=data,
        message="Engagement report retrieved successfully.",
    )


def _serialize_scores_summary(summary) -> ParticipationScoresSummaryOut:
    return ParticipationScoresSummaryOut(
        group_id=summary.group_id,
        generated_at=summary.generated_at,
        scores=[
            ParticipationScoreOut(
                user_id=score.user_id,
                name=score.name,
                predicted_score=score.predicted_score,
                contributor_tier=score.contributor_tier,
                features=score.features,
                generated_at=score.generated_at,
            )
            for score in summary.scores
        ],
        warnings=summary.warnings,
    )


async def _viewer_can_manage_group(
    group: ProjectGroup, user: User, db: AsyncSession
) -> bool:
    if group.owner_id == user.id:
        return True
    membership = await get_membership(group.id, user.id, db)
    return membership is not None and membership.role == GroupMemberRole.INSTRUCTOR


@router.post(
    "/{group_id}/participation-scores/generate",
    response_model=ApiResponse[ParticipationScoresSummaryOut],
    summary="Generate ML participation scores for group members",
    responses={
        403: {"description": "Only owner or instructor can generate scores."},
        409: {"description": "Scores already generated."},
        422: {"description": "Participation not synced."},
        503: {"description": "ML model unavailable."},
    },
)
async def generate_group_participation_scores(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner_or_instructor(group, current_user, db)
    summary = await generate_participation_scores(group, db)
    await db.commit()
    await db.refresh(group)
    return success(
        data=_serialize_scores_summary(summary),
        message="Participation scores generated successfully.",
    )


@router.get(
    "/{group_id}/participation-scores",
    response_model=ApiResponse[ParticipationScoresSummaryOut],
    summary="Get participation scores for group members",
)
async def get_group_participation_scores(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    can_manage = await _viewer_can_manage_group(group, current_user, db)
    summary = await get_participation_scores_for_group(
        group,
        db,
        viewer_user_id=current_user.id,
        viewer_is_manager=can_manage,
    )
    return success(
        data=_serialize_scores_summary(summary),
        message="Participation scores retrieved successfully.",
    )


@router.get(
    "/{group_id}/members/{user_id}/participation-score",
    response_model=ApiResponse[ParticipationScoreOut],
    summary="Get ML participation score for one member",
)
async def get_member_participation_score_endpoint(
    group_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_membership(group_id, current_user, db)
    can_manage = await _viewer_can_manage_group(group, current_user, db)
    if current_user.id != user_id and not can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own participation score.",
        )

    score = await get_member_participation_score(group, user_id, db)
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Participation score not found for this member.",
        )

    return success(
        data=ParticipationScoreOut(
            user_id=score.user_id,
            name=score.name,
            predicted_score=score.predicted_score,
            contributor_tier=score.contributor_tier,
            features=score.features,
            generated_at=score.generated_at,
        ),
        message="Participation score retrieved successfully.",
    )
