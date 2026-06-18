import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
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

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post(
    "",
    response_model=ApiResponse[GroupOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project group",
    responses={403: {"description": "Only students can create groups."}},
)
async def create_group(
    payload: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a group. The authenticated student becomes the group owner."""
    if current_user.role != RoleType.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can create groups.",
        )

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
        role=GroupMemberRole.STUDENT,
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
)
async def add_repo(
    group_id: str,
    payload: RepoLinkIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)
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
)
async def remove_repo(
    group_id: str,
    repo_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)
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
)
async def add_document(
    group_id: str,
    payload: DocumentLinkIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)
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
)
async def remove_document(
    group_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)
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
)
async def sync_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await get_group_or_404(group_id, db)
    await require_owner(group, current_user)
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
