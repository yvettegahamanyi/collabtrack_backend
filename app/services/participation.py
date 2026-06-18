from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    GroupGithubRepo,
    GroupGoogleDoc,
    GroupMembership,
    IntegrationProvider,
    ParticipationSnapshot,
    ProjectGroup,
    UserIntegration,
)
from app.schemas.participation import (
    ContributionsOut,
    GithubMetrics,
    GoogleDocsMetrics,
    MemberParticipationOut,
    SyncOut,
)
from app.services.github_sync import (
    GithubSyncResult,
    merge_github_results,
    sync_github_repo,
)
from app.services.google_sync import (
    GoogleSyncResult,
    fetch_google_doc_title,
    merge_google_results,
    sync_google_doc,
)
from app.services.integrations import (
    get_decrypted_access_token,
    get_user_integration,
    refresh_google_token_if_needed,
)

_GITHUB_API = "https://api.github.com"
_MIN_SYNC_INTERVAL_SECONDS = 60


async def link_github_repo(
    group: ProjectGroup,
    url: str,
    owner: str,
    repo: str,
    db: AsyncSession,
) -> GroupGithubRepo:
    access_token = await _find_github_token_for_group(group, db)
    default_branch = None
    if access_token:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{owner}/{repo}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if resp.status_code == 200:
                default_branch = resp.json().get("default_branch")

    record = GroupGithubRepo(
        group_id=group.id,
        owner=owner,
        repo=repo,
        default_branch=default_branch,
        url=url.rstrip("/"),
    )
    db.add(record)
    await db.flush()
    return record


async def link_google_doc(
    group: ProjectGroup,
    url: str,
    file_id: str,
    db: AsyncSession,
) -> GroupGoogleDoc:
    title = "Untitled Document"
    token = await _find_google_token_for_group(group, db)
    if token:
        title = await fetch_google_doc_title(token, file_id)

    record = GroupGoogleDoc(
        group_id=group.id,
        file_id=file_id,
        title=title,
        url=url,
    )
    db.add(record)
    await db.flush()
    return record


async def sync_group_participation(
    group: ProjectGroup, db: AsyncSession
) -> SyncOut:
    await _enforce_sync_rate_limit(group, db)

    memberships = await db.scalars(
        select(GroupMembership)
        .where(GroupMembership.group_id == group.id)
        .options(selectinload(GroupMembership.user))
    )
    member_list = list(memberships.all())
    if not member_list:
        synced_at = datetime.now(timezone.utc)
        return SyncOut(
            group_id=group.id, synced_at=synced_at, members_synced=0
        )

    github_logins: dict[str, str] = {}
    google_emails: dict[str, str] = {}
    integrations_by_user: dict[str, dict[str, UserIntegration | None]] = {}

    for membership in member_list:
        user = membership.user
        gh = await get_user_integration(
            db, user.id, IntegrationProvider.github
        )
        goog = await get_user_integration(
            db, user.id, IntegrationProvider.google
        )
        integrations_by_user[user.id] = {"github": gh, "google": goog}
        if gh and gh.provider_login:
            github_logins[user.id] = gh.provider_login
        if goog and goog.provider_email:
            google_emails[user.id] = goog.provider_email.lower()

    github_result = GithubSyncResult()
    repos = await db.scalars(
        select(GroupGithubRepo).where(GroupGithubRepo.group_id == group.id)
    )
    github_token = await _find_github_token_for_group(group, db)
    if github_token and github_logins:
        login_set = set(github_logins.values())
        for repo in repos.all():
            repo_result = await sync_github_repo(
                access_token=github_token,
                owner=repo.owner,
                repo=repo.repo,
                since=group.created_at,
                logins=login_set,
            )
            merge_github_results(github_result, repo_result)

    google_result = GoogleSyncResult()
    docs = await db.scalars(
        select(GroupGoogleDoc).where(GroupGoogleDoc.group_id == group.id)
    )
    google_token = await _find_google_token_for_group(group, db)
    if google_token and google_emails:
        email_set = set(google_emails.values())
        for doc in docs.all():
            doc_result = await sync_google_doc(
                access_token=google_token,
                file_id=doc.file_id,
                emails=email_set,
            )
            merge_google_results(google_result, doc_result)

    synced_at = datetime.now(timezone.utc)
    members_synced = 0

    for membership in member_list:
        user = membership.user
        user_integrations = integrations_by_user[user.id]
        gh_integration = user_integrations["github"]
        goog_integration = user_integrations["google"]

        metrics: dict = {}
        if gh_integration and gh_integration.provider_login:
            login = gh_integration.provider_login
            gh_metrics = github_result.by_login.get(login)
            if gh_metrics:
                metrics["github"] = gh_metrics.model_dump()
        if goog_integration and goog_integration.provider_email:
            email = goog_integration.provider_email.lower()
            g_metrics = google_result.by_email.get(email)
            if g_metrics:
                metrics["google_docs"] = g_metrics.model_dump()

        snapshot = await db.scalar(
            select(ParticipationSnapshot).where(
                ParticipationSnapshot.group_id == group.id,
                ParticipationSnapshot.user_id == user.id,
            )
        )
        if snapshot:
            snapshot.metrics = metrics
            snapshot.synced_at = synced_at
            db.add(snapshot)
        else:
            db.add(
                ParticipationSnapshot(
                    group_id=group.id,
                    user_id=user.id,
                    metrics=metrics,
                    synced_at=synced_at,
                )
            )
        members_synced += 1

    return SyncOut(
        group_id=group.id,
        synced_at=synced_at,
        members_synced=members_synced,
    )


async def get_contributions(
    group: ProjectGroup, db: AsyncSession
) -> ContributionsOut:
    memberships = await db.scalars(
        select(GroupMembership)
        .where(GroupMembership.group_id == group.id)
        .options(selectinload(GroupMembership.user))
    )
    snapshots = await db.scalars(
        select(ParticipationSnapshot).where(
            ParticipationSnapshot.group_id == group.id
        )
    )
    snapshot_by_user = {s.user_id: s for s in snapshots.all()}

    last_synced_at = None
    if snapshot_by_user:
        last_synced_at = max(s.synced_at for s in snapshot_by_user.values())

    members: list[MemberParticipationOut] = []
    for membership in memberships.all():
        user = membership.user
        gh = await get_user_integration(
            db, user.id, IntegrationProvider.github
        )
        goog = await get_user_integration(
            db, user.id, IntegrationProvider.google
        )
        snapshot = snapshot_by_user.get(user.id)

        github_metrics = None
        google_metrics = None
        if snapshot and snapshot.metrics:
            if "github" in snapshot.metrics:
                github_metrics = GithubMetrics(**snapshot.metrics["github"])
            if "google_docs" in snapshot.metrics:
                google_metrics = GoogleDocsMetrics(
                    **snapshot.metrics["google_docs"]
                )

        members.append(
            MemberParticipationOut(
                user_id=user.id,
                name=user.name,
                email=user.email,
                github_connected=gh is not None,
                google_connected=goog is not None,
                github_login=gh.provider_login if gh else None,
                google_email_matched=goog.email_matched if goog else None,
                github=github_metrics if gh else None,
                google_docs=google_metrics if goog else None,
            )
        )

    return ContributionsOut(
        group_id=group.id,
        last_synced_at=last_synced_at,
        members=members,
    )


async def get_member_participation(
    group: ProjectGroup, user_id: str, db: AsyncSession
) -> MemberParticipationOut:
    contributions = await get_contributions(group, db)
    for member in contributions.members:
        if member.user_id == user_id:
            return member
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Member not found in this group.",
    )


async def _find_github_token_for_group(
    group: ProjectGroup, db: AsyncSession
) -> str | None:
    memberships = await db.scalars(
        select(GroupMembership).where(GroupMembership.group_id == group.id)
    )
    for membership in memberships.all():
        integration = await get_user_integration(
            db, membership.user_id, IntegrationProvider.github
        )
        if integration:
            try:
                return await get_decrypted_access_token(integration)
            except ValueError:
                continue
    return None


async def _find_google_token_for_group(
    group: ProjectGroup, db: AsyncSession
) -> str | None:
    memberships = await db.scalars(
        select(GroupMembership).where(GroupMembership.group_id == group.id)
    )
    for membership in memberships.all():
        integration = await get_user_integration(
            db, membership.user_id, IntegrationProvider.google
        )
        if integration:
            try:
                return await refresh_google_token_if_needed(integration, db)
            except (ValueError, HTTPException):
                continue
    return None


async def _enforce_sync_rate_limit(
    group: ProjectGroup, db: AsyncSession
) -> None:
    latest = await db.scalar(
        select(ParticipationSnapshot.synced_at)
        .where(ParticipationSnapshot.group_id == group.id)
        .order_by(ParticipationSnapshot.synced_at.desc())
        .limit(1)
    )
    if latest is None:
        return
    elapsed = (datetime.now(timezone.utc) - latest).total_seconds()
    if elapsed < _MIN_SYNC_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Please wait {_MIN_SYNC_INTERVAL_SECONDS - int(elapsed)} "
                "seconds before syncing again."
            ),
        )
