import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    ContributionReport,
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
    GithubSyncEvent,
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

def _normalize_github_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


_MIN_LOGIN_MATCH_SCORE = 50
_OAUTH_LOGIN_SCORE = 100
_EXACT_EMAIL_SCORE = 90
_PUBLIC_EMAIL_SCORE = 85
_EXACT_IDENTIFIER_SCORE = 80
_SUBSTRING_IDENTIFIER_SCORE = 70
_MIN_SUBSTRING_LENGTH = 6


@dataclass
class PlatformIdentity:
    github_email: str | None = None
    google_docs_email: str | None = None
    google_meet_email: str | None = None


def _register_platform_email_aliases(
    *,
    identity: PlatformIdentity | None,
    canonical_email: str,
    email_canonical: dict[str, str],
) -> None:
    """Map every known platform email for a member to their docs signup email."""
    email_canonical[canonical_email] = canonical_email
    if not identity:
        return
    for alias in (
        identity.github_email,
        identity.google_docs_email,
        identity.google_meet_email,
    ):
        if alias:
            email_canonical[alias.lower()] = canonical_email


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
    *,
    platform_identities: dict[str, PlatformIdentity] | None = None,
) -> tuple[dict[str, GithubMetrics], dict[str, list[GithubSyncEvent]]]:
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

        identity = (platform_identities or {}).get(user.id)
        github_email = (
            identity.github_email.lower()
            if identity and identity.github_email
            else user.email.lower()
        )
        if github_email in github_result.by_email:
            candidates.append((_EXACT_EMAIL_SCORE, user.id, f"email:{github_email}"))

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
    events_by_user: dict[str, list[GithubSyncEvent]] = {}

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
            events_by_user[user_id] = [
                GithubSyncEvent(**event.to_dict())
                for event in github_result.events_by_email.get(email_key, [])
            ]
            continue
        if login in assigned_logins:
            continue
        metrics = github_result.by_login.get(login)
        if metrics is None:
            continue
        assigned_users.add(user_id)
        assigned_logins.add(login)
        metrics_by_user[user_id] = metrics
        events_by_user[user_id] = [
            GithubSyncEvent(**event.to_dict())
            for event in github_result.events_by_login.get(login, [])
        ]

    return metrics_by_user, events_by_user


def _student_memberships(
    memberships: list[GroupMembership],
) -> list[GroupMembership]:
    return [
        membership
        for membership in memberships
        if membership.role != GroupMemberRole.INSTRUCTOR
    ]


async def _contribution_report_member_cache(
    group_id: str, db: AsyncSession
) -> dict[str, dict]:
    report = await db.scalar(
        select(ContributionReport)
        .where(ContributionReport.group_id == group_id)
        .order_by(ContributionReport.generated_at.desc())
        .limit(1)
    )
    if report is None or not report.final_calculated_scores:
        return {}

    members = report.final_calculated_scores.get("members") or []
    if not isinstance(members, list):
        return {}
    return {
        str(member.get("user_id")): member
        for member in members
        if isinstance(member, dict) and member.get("user_id")
    }


def _merge_member_metrics(
    *,
    github_metrics: GithubMetrics | None,
    github_events: list[GithubSyncEvent],
    google_metrics: GoogleDocsMetrics | None,
    google_events: list[GoogleDocSyncEvent],
    meeting_engagement: MeetingEngagementMetrics | None,
    cached_member: dict | None,
) -> tuple[
    GithubMetrics | None,
    list[GithubSyncEvent],
    GoogleDocsMetrics | None,
    list[GoogleDocSyncEvent],
    MeetingEngagementMetrics | None,
]:
    if cached_member is None:
        return (
            github_metrics,
            github_events,
            google_metrics,
            google_events,
            meeting_engagement,
        )

    if github_metrics is None and cached_member.get("github"):
        github_metrics = GithubMetrics(**cached_member["github"])
    if not github_events and cached_member.get("github_events"):
        github_events = [
            GithubSyncEvent(**event)
            for event in cached_member["github_events"]
            if isinstance(event, dict)
        ]
    if google_metrics is None and cached_member.get("google_docs"):
        google_metrics = GoogleDocsMetrics(**cached_member["google_docs"])
    if not google_events and cached_member.get("google_docs_events"):
        google_events = [
            GoogleDocSyncEvent(**event)
            for event in cached_member["google_docs_events"]
            if isinstance(event, dict)
        ]
    if meeting_engagement is None and cached_member.get("meeting_engagement"):
        meeting_engagement = MeetingEngagementMetrics(
            **cached_member["meeting_engagement"]
        )

    return (
        github_metrics,
        github_events,
        google_metrics,
        google_events,
        meeting_engagement,
    )


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
    existing = await db.scalar(
        select(GroupGithubRepo).where(
            GroupGithubRepo.group_id == group.id,
            GroupGithubRepo.owner == owner,
            GroupGithubRepo.repo == repo,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Repository {owner}/{repo} is already linked to this group.",
        )

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
    existing = await db.scalar(
        select(GroupGoogleDoc).where(
            GroupGoogleDoc.group_id == group.id,
            GroupGoogleDoc.file_id == file_id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Google Doc is already linked to this group.",
        )

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
    group: ProjectGroup,
    db: AsyncSession,
    *,
    platform_identities: dict[str, PlatformIdentity] | None = None,
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
        identity = (platform_identities or {}).get(user.id)
        signup = (
            identity.google_docs_email.lower()
            if identity and identity.google_docs_email
            else user.email.lower()
        )
        signup_emails.add(signup)
        _register_platform_email_aliases(
            identity=identity,
            canonical_email=signup,
            email_canonical=email_canonical,
        )
        if gh and gh.provider_login and not identity:
            github_logins[user.id] = gh.provider_login
        elif identity and identity.github_email:
            github_emails[user.id] = identity.github_email.lower()
        else:
            github_emails[user.id] = signup
        if goog and goog.provider_email and not identity:
            oauth_email = goog.provider_email.lower()
            google_emails[user.id] = oauth_email
            email_canonical[oauth_email] = signup
        elif identity and identity.google_docs_email:
            docs_email = identity.google_docs_email.lower()
            google_emails[user.id] = docs_email
            email_canonical[docs_email] = signup
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
    if doc_list and not google_tokens:
        sync_warnings.append(
            "No group member has Google connected. Connect Google in Settings "
            "so version history can be read."
        )
    if google_tokens and signup_emails:
        name_to_email = {}
        for membership in member_list:
            identity = (platform_identities or {}).get(membership.user.id)
            if identity and identity.google_docs_email:
                lookup = identity.google_docs_email.lower()
            else:
                lookup = membership.user.email.lower()
            if membership.user.name:
                name_to_email[
                    " ".join(membership.user.name.strip().lower().split())
                ] = lookup
        for doc in doc_list:
            doc_synced = False
            last_status = None
            best_result: GoogleSyncResult | None = None
            best_status = None
            best_score = -1
            best_matched_count = -1
            activity_scope_available = False
            people_lookup_scope_available = False
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
                if doc_status.activity_scope_granted and doc_status.activity_status != 403:
                    activity_scope_available = True
                if doc_status.people_lookup_scope_granted:
                    people_lookup_scope_available = True
                match_score = sum(
                    metrics.edits + metrics.comments
                    for metrics in doc_result.by_email.values()
                )
                matched_count = len(doc_result.by_email)
                if (
                    doc_status.revisions_status != 200
                    and doc_status.activity_status != 200
                ):
                    continue
                doc_synced = True
                prefer_token = match_score > best_score or (
                    match_score == best_score
                    and matched_count > best_matched_count
                ) or (
                    match_score == best_score
                    and matched_count == best_matched_count
                    and doc_status.people_lookup_scope_granted
                    and (best_status is None or not best_status.people_lookup_scope_granted)
                )
                if prefer_token:
                    best_result = doc_result
                    best_status = doc_status
                    best_score = match_score
                    best_matched_count = matched_count

            if best_result is not None:
                merge_google_results(google_result, best_result)

            if google_tokens and not people_lookup_scope_available:
                sync_warnings.append(
                    f'Edit counts for "{doc.title}" may miss domain-wide editors. '
                    "Disconnect then Connect Google in Settings so CollabTrack "
                    "can access contacts.readonly and directory.readonly scopes, "
                    "then sync again."
                )
            if google_tokens and not activity_scope_available:
                sync_warnings.append(
                    f'Edit counts for "{doc.title}" may be incomplete. '
                    "Reconnect Google in Settings (Disconnect → Connect) so "
                    "CollabTrack can access Drive Activity. For domain-wide docs, "
                    "the file owner is matched automatically; other editors may "
                    "need to connect Google or be individually shared on the doc."
                )

            status_for_warnings = best_status or last_status
            if (
                status_for_warnings is not None
                and status_for_warnings.activity_status == 200
                and status_for_warnings.activity_edit_count > status_for_warnings.revision_count
                and status_for_warnings.activity_source == "revisions"
                and not status_for_warnings.matched_emails
            ):
                sync_warnings.append(
                    f'Found {status_for_warnings.activity_edit_count} edit events in Drive '
                    f'Activity for "{doc.title}", but could not match authors to '
                    "group members. Reconnect Google and ensure collaborators use "
                    "their school emails on the document."
                )
            if (
                best_result is not None
                and best_status is not None
                and best_status.activity_status == 200
                and best_status.activity_edit_count > 0
            ):
                matched_edits = sum(
                    metrics.edits for metrics in best_result.by_email.values()
                )
                if matched_edits < best_status.activity_edit_count:
                    sync_warnings.append(
                        f'Only {matched_edits} of {best_status.activity_edit_count} '
                        f'doc edits for "{doc.title}" were matched to group members. '
                        "Reconnect Google (Disconnect → Connect) with directory access, "
                        "then re-collect. Ensure each student’s google_docs_email in the "
                        "identity CSV matches the email they use on the document."
                    )

            if not doc_synced and last_status is not None:
                sync_warnings.append(
                    _google_doc_sync_warning(doc.title, last_status)
                )


    synced_at = datetime.now(timezone.utc)
    members_synced = 0

    github_metrics_by_user, github_events_by_user = _assign_github_metrics(
        member_list,
        integrations_by_user,
        github_result,
        platform_identities=platform_identities,
    )

    if repo_list and not github_metrics_by_user:
        sync_warnings.append(
            "A GitHub repository is linked but no commits could be matched to "
            "group members. This usually happens when the emails used to list "
            "the members don't match the GitHub commit-author emails. Ask "
            "members to connect GitHub in Settings, or verify their commit emails."
        )

    for membership in member_list:
        user = membership.user
        identity = (platform_identities or {}).get(user.id)

        metrics: dict = {}
        gh_metrics = github_metrics_by_user.get(user.id)
        gh_events = github_events_by_user.get(user.id, [])
        if gh_metrics is not None:
            metrics["github"] = gh_metrics.model_dump()
        if gh_events:
            metrics["github_events"] = [event.model_dump() for event in gh_events]

        lookup_email = (
            identity.google_docs_email.lower()
            if identity and identity.google_docs_email
            else user.email.lower()
        )
        g_metrics = google_result.by_email.get(lookup_email)
        g_events = google_result.events_by_email.get(lookup_email, [])
        if g_metrics:
            metrics["google_docs"] = g_metrics.model_dump()
        if g_events:
            metrics["google_docs_events"] = [event.to_dict() for event in g_events]

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
    report_member_cache = await _contribution_report_member_cache(group.id, db)

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
        github_events: list[GithubSyncEvent] = []
        google_metrics = None
        google_events: list[GoogleDocSyncEvent] = []
        if snapshot and snapshot.metrics:
            if "github" in snapshot.metrics:
                github_metrics = GithubMetrics(**snapshot.metrics["github"])
            if "github_events" in snapshot.metrics:
                github_events = [
                    GithubSyncEvent(**event)
                    for event in snapshot.metrics["github_events"]
                ]
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

        github_metrics, github_events, google_metrics, google_events, meeting_engagement = (
            _merge_member_metrics(
                github_metrics=github_metrics,
                github_events=github_events,
                google_metrics=google_metrics,
                google_events=google_events,
                meeting_engagement=meeting_engagement,
                cached_member=report_member_cache.get(user.id),
            )
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
                github_events=github_events,
                google_docs=google_metrics,
                google_docs_events=google_events,
                meeting_engagement=meeting_engagement,
            )
        )

    warnings: list[str] = []
    if last_synced_at is not None:
        repo_count = await db.scalar(
            select(func.count())
            .select_from(GroupGithubRepo)
            .where(GroupGithubRepo.group_id == group.id)
        )
        if repo_count and not any(member.github is not None for member in members):
            warnings.append(
                "A GitHub repository is linked but no commits could be matched to "
                "group members. This usually happens when the emails used to list "
                "the members don't match the GitHub commit-author emails. The report "
                "was generated without GitHub contribution data."
            )
        doc_count = await db.scalar(
            select(func.count())
            .select_from(GroupGoogleDoc)
            .where(GroupGoogleDoc.group_id == group.id)
        )
        if doc_count and not any(member.google_docs is not None for member in members):
            warnings.append(
                "A Google Doc is linked but no edits could be matched to group "
                "members. The report was generated without Google Docs contribution "
                "data."
            )

    return ContributionsOut(
        group_id=group.id,
        last_synced_at=last_synced_at,
        members=members,
        warnings=warnings,
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
    user_ids: list[str] = [group.owner_id]
    memberships = await db.scalars(
        select(GroupMembership).where(GroupMembership.group_id == group.id)
    )
    user_ids.extend(membership.user_id for membership in memberships.all())
    seen: set[str] = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        integration = await get_user_integration(
            db, user_id, IntegrationProvider.github
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
    user_ids: list[str] = [group.owner_id]
    memberships = await db.scalars(
        select(GroupMembership).where(GroupMembership.group_id == group.id)
    )
    user_ids.extend(membership.user_id for membership in memberships.all())
    tokens: list[tuple[str, str]] = []
    seen_emails: set[str] = set()
    seen_users: set[str] = set()
    for user_id in user_ids:
        if user_id in seen_users:
            continue
        seen_users.add(user_id)
        integration = await get_user_integration(
            db, user_id, IntegrationProvider.google
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
