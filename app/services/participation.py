import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    GroupGithubRepo,
    GroupGoogleDoc,
    GroupMemberRole,
    GroupMembership,
    IntegrationProvider,
    ParticipationSnapshot,
    ProjectGroup,
    User,
    UserIntegration,
)
from app.schemas.meetings import MeetingEngagementMetrics
from app.schemas.participation import (
    ContributionsOut,
    GithubMetrics,
    GoogleDocsMetrics,
    GoogleDocSyncEvent,
    MemberParticipationOut,
    SyncOut,
)
from app.services.github_sync import (
    GithubSyncResult,
    merge_github_results,
    sync_github_repo,
    sync_github_repo_by_email,
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
from app.services.meetings import get_engagement_scores_by_user

_GITHUB_API = "https://api.github.com"
_MIN_SYNC_INTERVAL_SECONDS = 60
_DEBUG_LOG = "/Users/gahamanyi/Documents/alu/CAPSTON PROJECT/.cursor/debug-29e602.log"


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    try:
        Path(_DEBUG_LOG).parent.mkdir(parents=True, exist_ok=True)
        with open(_DEBUG_LOG, "a", encoding="utf-8") as log_file:
            log_file.write(
                json.dumps(
                    {
                        "sessionId": "29e602",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                        "runId": "google-debug",
                    }
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion


def _normalize_github_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


_MIN_LOGIN_MATCH_SCORE = 50
_OAUTH_LOGIN_SCORE = 100
_EXACT_EMAIL_SCORE = 90
_PUBLIC_EMAIL_SCORE = 85
_EXACT_IDENTIFIER_SCORE = 80
_SUBSTRING_IDENTIFIER_SCORE = 70
_MIN_SUBSTRING_LENGTH = 6


def _score_github_login_for_user(
    login: str,
    *,
    user: User,
    git_emails: set[str] | None = None,
    public_email: str | None = None,
) -> int:
    email = user.email.lower()
    email_local = email.split("@", 1)[0]
    login_norm = _normalize_github_identifier(login)
    email_norm = _normalize_github_identifier(email_local)

    if public_email and public_email.lower() == email:
        return _PUBLIC_EMAIL_SCORE

    if git_emails and email in git_emails:
        return _EXACT_EMAIL_SCORE

    if login_norm and email_norm:
        if login_norm == email_norm:
            return _EXACT_IDENTIFIER_SCORE
        if (
            len(login_norm) >= _MIN_SUBSTRING_LENGTH
            and login_norm in email_norm
        ):
            return _SUBSTRING_IDENTIFIER_SCORE
        if (
            len(email_norm) >= _MIN_SUBSTRING_LENGTH
            and email_norm in login_norm
        ):
            return _SUBSTRING_IDENTIFIER_SCORE

    best_name_score = 0
    if user.name:
        for token in user.name.split():
            token_norm = _normalize_github_identifier(token)
            if len(token_norm) >= 4 and token_norm in login_norm:
                best_name_score = max(best_name_score, 50 + len(token_norm))

    return best_name_score


def _assign_github_metrics(
    member_list: list[GroupMembership],
    integrations_by_user: dict[str, dict[str, UserIntegration | None]],
    github_result: GithubSyncResult,
) -> dict[str, GithubMetrics]:
    candidates: list[tuple[int, str, str]] = []

    for membership in member_list:
        user = membership.user
        gh_integration = integrations_by_user[user.id]["github"]
        if gh_integration and gh_integration.provider_login:
            oauth_login = gh_integration.provider_login
            if oauth_login in github_result.by_login:
                candidates.append(
                    (_OAUTH_LOGIN_SCORE, user.id, oauth_login)
                )

        email = user.email.lower()
        if email in github_result.by_email:
            candidates.append((_EXACT_EMAIL_SCORE, user.id, f"email:{email}"))

        for login in github_result.by_login:
            score = _score_github_login_for_user(
                login,
                user=user,
                git_emails=github_result.login_git_emails.get(login, set()),
                public_email=github_result.login_public_emails.get(login),
            )
            if score >= _MIN_LOGIN_MATCH_SCORE:
                candidates.append((score, user.id, login))

    candidates.sort(key=lambda item: item[0], reverse=True)

    assigned_users: set[str] = set()
    assigned_logins: set[str] = set()
    metrics_by_user: dict[str, GithubMetrics] = {}

    for score, user_id, login in candidates:
        if user_id in assigned_users:
            continue
        if login.startswith("email:"):
            email_key = login.removeprefix("email:")
            if email_key in assigned_logins:
                continue
            assigned_users.add(user_id)
            assigned_logins.add(email_key)
            metrics_by_user[user_id] = github_result.by_email[email_key]
            continue
        if login in assigned_logins:
            continue
        metrics = github_result.by_login.get(login)
        if metrics is None:
            continue
        assigned_users.add(user_id)
        assigned_logins.add(login)
        metrics_by_user[user_id] = metrics

    return metrics_by_user


def _student_memberships(
    memberships: list[GroupMembership],
) -> list[GroupMembership]:
    return [
        membership
        for membership in memberships
        if membership.role != GroupMemberRole.INSTRUCTOR
    ]


def _google_doc_sync_warning(doc_title: str, doc_status) -> str:
    if doc_status.failure_kind == "drive_api_disabled":
        return (
            f'Could not read activity for "{doc_title}". '
            "Enable Google Drive API and Google Drive Activity API in your "
            "Google Cloud OAuth project, then wait a few minutes and sync again."
        )
    if doc_status.failure_kind == "insufficient_scope_or_access":
        return (
            f'Could not read activity for "{doc_title}". '
            "Reconnect Google in Settings (Disconnect → Connect) so CollabTrack "
            "can request Drive and Drive Activity access, then sync again."
        )
    return (
        f'Could not read version history for "{doc_title}". '
        "Share the document with a group member who has Google connected, "
        f"then sync again. {doc_status.error or ''}".strip()
    )


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
    member_list = _student_memberships(list(memberships.all()))
    if not member_list:
        synced_at = datetime.now(timezone.utc)
        return SyncOut(
            group_id=group.id, synced_at=synced_at, members_synced=0
        )

    github_logins: dict[str, str] = {}
    github_emails: dict[str, str] = {}
    google_emails: dict[str, str] = {}
    email_canonical: dict[str, str] = {}
    signup_emails: set[str] = set()
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
        signup = user.email.lower()
        signup_emails.add(signup)
        email_canonical[signup] = signup
        if gh and gh.provider_login:
            github_logins[user.id] = gh.provider_login
        else:
            github_emails[user.id] = signup
        if goog and goog.provider_email:
            oauth_email = goog.provider_email.lower()
            google_emails[user.id] = oauth_email
            email_canonical[oauth_email] = signup
        else:
            google_emails[user.id] = signup

    github_result = GithubSyncResult()
    repos = await db.scalars(
        select(GroupGithubRepo).where(GroupGithubRepo.group_id == group.id)
    )
    github_token = await _find_github_token_for_group(group, db)
    repo_list = list(repos.all())
    if github_token and github_logins:
        login_set = set(github_logins.values())
        for repo in repo_list:
            repo_result = await sync_github_repo(
                access_token=github_token,
                owner=repo.owner,
                repo=repo.repo,
                since=None,
                logins=login_set,
            )
            merge_github_results(github_result, repo_result)
    if github_token and github_emails:
        email_set = set(github_emails.values())
        for repo in repo_list:
            repo_result = await sync_github_repo_by_email(
                access_token=github_token,
                owner=repo.owner,
                repo=repo.repo,
                emails=email_set,
                since=None,
            )
            merge_github_results(github_result, repo_result)

    google_result = GoogleSyncResult()
    docs = await db.scalars(
        select(GroupGoogleDoc).where(GroupGoogleDoc.group_id == group.id)
    )
    doc_list = list(docs.all())
    google_tokens = await _find_google_tokens_for_group(group, db)
    sync_warnings: list[str] = []
    # #region agent log
    _debug_log(
        "G1",
        "participation.py:sync_group_participation:google_setup",
        "Google sync prerequisites",
        {
            "group_id": group.id,
            "google_token_count": len(google_tokens),
            "google_token_emails": [email for email, _ in google_tokens],
            "doc_count": len(doc_list),
            "doc_file_ids": [doc.file_id for doc in doc_list],
            "student_emails": sorted(signup_emails),
            "email_alias_count": len(email_canonical),
            "repo_count": len(repo_list),
        },
    )
    # #endregion
    if doc_list and not google_tokens:
        sync_warnings.append(
            "No group member has Google connected. Connect Google in Settings "
            "so version history can be read."
        )
    if google_tokens and signup_emails:
        name_to_email = {
            " ".join(membership.user.name.strip().lower().split()): membership.user.email.lower()
            for membership in member_list
        }
        for doc in doc_list:
            doc_synced = False
            last_status = None
            best_result: GoogleSyncResult | None = None
            best_score = -1
            activity_scope_missing = False
            people_lookup_scope_missing = False
            for provider_email, token in google_tokens:
                doc_result, doc_status = await sync_google_doc(
                    access_token=token,
                    file_id=doc.file_id,
                    signup_emails=signup_emails,
                    email_canonical=email_canonical,
                    token_holder_email=provider_email,
                    name_to_email=name_to_email,
                )
                last_status = doc_status
                if (
                    not doc_status.activity_scope_granted
                    or doc_status.activity_status == 403
                ):
                    activity_scope_missing = True
                if not doc_status.people_lookup_scope_granted:
                    people_lookup_scope_missing = True
                match_score = sum(
                    metrics.edits + metrics.comments
                    for metrics in doc_result.by_email.values()
                )
                # #region agent log
                _debug_log(
                    "H5",
                    "participation.py:sync_group_participation:token_attempt",
                    "Google token attempt for doc sync",
                    {
                        "group_id": group.id,
                        "file_id": doc.file_id,
                        "provider_email": provider_email,
                        "revisions_status": doc_status.revisions_status,
                        "activity_status": doc_status.activity_status,
                        "activity_scope_granted": doc_status.activity_scope_granted,
                        "people_lookup_scope_granted": doc_status.people_lookup_scope_granted,
                        "activity_source": doc_status.activity_source,
                        "failure_kind": doc_status.failure_kind,
                        "match_score": match_score,
                        "revision_count": doc_status.revision_count,
                        "activity_edit_count": doc_status.activity_edit_count,
                        "matched_emails": doc_status.matched_emails,
                    },
                )
                # #endregion
                if (
                    doc_status.revisions_status != 200
                    and doc_status.activity_status != 200
                ):
                    continue
                doc_synced = True
                if match_score > best_score:
                    best_result = doc_result
                    best_score = match_score

            if best_result is not None:
                merge_google_results(google_result, best_result)

            if people_lookup_scope_missing:
                sync_warnings.append(
                    f'Edit counts for "{doc.title}" may miss domain-wide editors. '
                    "Disconnect then Connect Google in Settings so CollabTrack "
                    "can access contacts.readonly and directory.readonly scopes, "
                    "then sync again."
                )
            if activity_scope_missing:
                sync_warnings.append(
                    f'Edit counts for "{doc.title}" may be incomplete. '
                    "Reconnect Google in Settings (Disconnect → Connect) so "
                    "CollabTrack can access Drive Activity. For domain-wide docs, "
                    "the file owner is matched automatically; other editors may "
                    "need to connect Google or be individually shared on the doc."
                )

            if (
                last_status is not None
                and last_status.activity_status == 200
                and last_status.activity_edit_count > last_status.revision_count
                and last_status.activity_source == "revisions"
                and not last_status.matched_emails
            ):
                sync_warnings.append(
                    f'Found {last_status.activity_edit_count} edit events in Drive '
                    f'Activity for "{doc.title}", but could not match authors to '
                    "group members. Reconnect Google and ensure collaborators use "
                    "their school emails on the document."
                )

            if not doc_synced and last_status is not None:
                sync_warnings.append(
                    _google_doc_sync_warning(doc.title, last_status)
                )

    # #region agent log
    _debug_log(
        "G2",
        "participation.py:sync_group_participation:google_results",
        "Google sync aggregated results",
        {
            "group_id": group.id,
            "by_email_keys": list(google_result.by_email.keys()),
            "by_email_metrics": {
                email: metrics.model_dump()
                for email, metrics in google_result.by_email.items()
            },
        },
    )
    # #endregion

    synced_at = datetime.now(timezone.utc)
    members_synced = 0
    google_member_assignment: list[dict] = []

    github_metrics_by_user = _assign_github_metrics(
        member_list, integrations_by_user, github_result
    )

    for membership in member_list:
        user = membership.user
        user_integrations = integrations_by_user[user.id]
        goog_integration = user_integrations["google"]

        metrics: dict = {}
        gh_metrics = github_metrics_by_user.get(user.id)
        if gh_metrics is not None:
            metrics["github"] = gh_metrics.model_dump()

        lookup_email = user.email.lower()
        g_metrics = google_result.by_email.get(lookup_email)
        g_events = google_result.events_by_email.get(lookup_email, [])
        if g_metrics:
            metrics["google_docs"] = g_metrics.model_dump()
        if g_events:
            metrics["google_docs_events"] = [event.to_dict() for event in g_events]
        google_member_assignment.append(
            {
                "user_id": user.id,
                "lookup_email": lookup_email,
                "has_google_docs_metrics": g_metrics is not None,
                "edits": g_metrics.edits if g_metrics else 0,
                "comments": g_metrics.comments if g_metrics else 0,
            }
        )

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

    # #region agent log
    _debug_log(
        "G3",
        "participation.py:sync_group_participation:google_assignment",
        "Per-member Google Docs metric assignment",
        {
            "group_id": group.id,
            "members_with_google_metrics": sum(
                1 for entry in google_member_assignment if entry["has_google_docs_metrics"]
            ),
            "per_member": google_member_assignment,
        },
    )
    # #endregion

    return SyncOut(
        group_id=group.id,
        synced_at=synced_at,
        members_synced=members_synced,
        warnings=sync_warnings,
    )


async def get_contributions(
    group: ProjectGroup, db: AsyncSession
) -> ContributionsOut:
    memberships = await db.scalars(
        select(GroupMembership)
        .where(GroupMembership.group_id == group.id)
        .options(selectinload(GroupMembership.user))
    )
    student_members = _student_memberships(list(memberships.all()))
    student_user_ids = {membership.user_id for membership in student_members}

    snapshot_by_user: dict[str, ParticipationSnapshot] = {}
    if student_user_ids:
        snapshots = await db.scalars(
            select(ParticipationSnapshot).where(
                ParticipationSnapshot.group_id == group.id,
                ParticipationSnapshot.user_id.in_(student_user_ids),
            )
        )
        snapshot_by_user = {s.user_id: s for s in snapshots.all()}

    engagement_by_user = await get_engagement_scores_by_user(group.id, db)

    last_synced_at = None
    if snapshot_by_user:
        last_synced_at = max(s.synced_at for s in snapshot_by_user.values())

    members: list[MemberParticipationOut] = []
    for membership in student_members:
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
        google_events: list[GoogleDocSyncEvent] = []
        if snapshot and snapshot.metrics:
            if "github" in snapshot.metrics:
                github_metrics = GithubMetrics(**snapshot.metrics["github"])
            if "google_docs" in snapshot.metrics:
                google_metrics = GoogleDocsMetrics(
                    **snapshot.metrics["google_docs"]
                )
            if "google_docs_events" in snapshot.metrics:
                google_events = [
                    GoogleDocSyncEvent(**event)
                    for event in snapshot.metrics["google_docs_events"]
                ]

        engagement_score = engagement_by_user.get(user.id)
        meeting_engagement = None
        if engagement_score is not None:
            meeting_engagement = MeetingEngagementMetrics(
                attendance_ratio=engagement_score.attendance_ratio,
                speaking_ratio=engagement_score.speaking_ratio,
                chat_participation=engagement_score.chat_participation,
                meeting_lead_count=engagement_score.meeting_lead_count,
                sessions_attended=engagement_score.sessions_attended,
                total_sessions=engagement_score.total_sessions,
            )

        members.append(
            MemberParticipationOut(
                user_id=user.id,
                name=user.name,
                email=user.email,
                account_status=user.account_status,
                github_connected=gh is not None,
                google_connected=goog is not None,
                github_login=gh.provider_login if gh else None,
                google_email_matched=goog.email_matched if goog else None,
                github=github_metrics,
                google_docs=google_metrics,
                google_docs_events=google_events,
                meeting_engagement=meeting_engagement,
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
    membership = await db.scalar(
        select(GroupMembership).where(
            GroupMembership.group_id == group.id,
            GroupMembership.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this group.",
        )
    if membership.role == GroupMemberRole.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Participation is not tracked for instructors.",
        )

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


async def _find_google_tokens_for_group(
    group: ProjectGroup, db: AsyncSession
) -> list[tuple[str, str]]:
    """Return (provider_email, access_token) for each member with Google connected."""
    memberships = await db.scalars(
        select(GroupMembership).where(GroupMembership.group_id == group.id)
    )
    tokens: list[tuple[str, str]] = []
    seen_emails: set[str] = set()
    for membership in memberships.all():
        integration = await get_user_integration(
            db, membership.user_id, IntegrationProvider.google
        )
        if integration is None:
            continue
        provider_email = (integration.provider_email or "").lower()
        if provider_email and provider_email in seen_emails:
            continue
        try:
            token = await refresh_google_token_if_needed(integration, db)
        except (ValueError, HTTPException):
            continue
        if provider_email:
            seen_emails.add(provider_email)
        tokens.append((provider_email or "unknown", token))
    return tokens


async def _find_google_token_for_group(
    group: ProjectGroup, db: AsyncSession
) -> str | None:
    tokens = await _find_google_tokens_for_group(group, db)
    return tokens[0][1] if tokens else None


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
