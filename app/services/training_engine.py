import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    CollabTrackDataset,
    GroupMemberRole,
    GroupMembership,
    MeetingNameMapping,
    MeetingRawMetric,
    MeetingSession,
    MeetingSessionStatus,
    ProjectGroup,
    RoleType,
    ServiceType,
    TrainingCollection,
    TrainingCollectionMember,
    TrainingCollectionStatus,
    User,
)
from app.schemas.dataset import CollabTrackDatasetOut
from app.schemas.report import MeetingInputMeta
from app.schemas.training import IdentityMemberPreview, TrainingCollectionDetailOut
from app.services.dataset_features import (
    build_group_activity_totals,
    compute_dataset_features,
)
from app.services.integrations import parse_github_repo_url, parse_google_doc_url
from app.services.meetings import create_meeting_session, upload_meeting_files
from app.services.participation import (
    PlatformIdentity,
    get_contributions,
    link_github_repo,
    link_google_doc,
    sync_group_participation,
)
from app.schemas.meetings import MeetingSessionCreate

_NAME_HEADERS = {"name"}
_GITHUB_HEADERS = {"github_email", "github email"}
_GOOGLE_DOCS_HEADERS = {
    "google_docs_email",
    "googledocs_email",
    "google docs email",
    "googledocsemail",
}
_GOOGLE_MEET_HEADERS = {
    "google_meet_email",
    "googlemeet_email",
    "google meet email",
    "googlemeetemail",
}


@dataclass
class IdentityRow:
    name: str
    github_email: str | None
    google_docs_email: str | None
    google_meet_email: str | None


def parse_identity_csv(content: str) -> list[IdentityRow]:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Identity CSV is empty or missing a header row.",
        )

    header_map = {name.strip().lower(): name for name in reader.fieldnames}
    name_col = next((header_map[key] for key in _NAME_HEADERS if key in header_map), None)
    if name_col is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Identity CSV must include a 'name' column.",
        )

    github_col = next(
        (header_map[key] for key in _GITHUB_HEADERS if key in header_map), None
    )
    docs_col = next(
        (header_map[key] for key in _GOOGLE_DOCS_HEADERS if key in header_map), None
    )
    meet_col = next(
        (header_map[key] for key in _GOOGLE_MEET_HEADERS if key in header_map), None
    )

    rows: list[IdentityRow] = []
    seen_emails: set[str] = set()

    for row_number, row in enumerate(reader, start=2):
        name = (row.get(name_col) or "").strip()
        if not name:
            continue

        github_email = _normalize_email(row.get(github_col)) if github_col else None
        google_docs_email = _normalize_email(row.get(docs_col)) if docs_col else None
        google_meet_email = _normalize_email(row.get(meet_col)) if meet_col else None

        if not any([github_email, google_docs_email, google_meet_email]):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Identity CSV row {row_number}: at least one platform email "
                    "is required."
                ),
            )

        row_emails = {
            email
            for email in (github_email, google_docs_email, google_meet_email)
            if email
        }

        for email in row_emails:
            if email in seen_emails:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Duplicate platform email '{email}' in identity CSV.",
                )

        seen_emails.update(row_emails)

        rows.append(
            IdentityRow(
                name=name,
                github_email=github_email,
                google_docs_email=google_docs_email,
                google_meet_email=google_meet_email,
            )
        )

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Identity CSV contains no member rows.",
        )

    return rows


def _normalize_email(value: object | None) -> str | None:
    if value is None:
        return None
    email = str(value).strip().lower()
    return email or None


async def allocate_dataset_group_id(db: AsyncSession) -> str:
    rows = await db.scalars(select(CollabTrackDataset.group_id))
    numeric_ids = [int(value) for value in rows.all() if value.isdigit()]
    return str(max(numeric_ids, default=0) + 1)


async def collect_training_data(
    *,
    collector: User,
    identity_csv: UploadFile,
    github_urls: list[str],
    google_doc_urls: list[str],
    meetings_meta: list[MeetingInputMeta],
    meeting_files: list[tuple[UploadFile, UploadFile, UploadFile | None]],
    db: AsyncSession,
) -> TrainingCollectionDetailOut:
    if not github_urls and not google_doc_urls and not meetings_meta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one GitHub URL, Google Doc URL, or meeting.",
        )

    if meetings_meta and len(meeting_files) != len(meetings_meta):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Each meeting requires attendance, transcript, and chat files.",
        )

    content = (await identity_csv.read()).decode("utf-8")
    identity_rows = parse_identity_csv(content)
    dataset_group_id = await allocate_dataset_group_id(db)

    group = ProjectGroup(
        group_name=f"Training Group {dataset_group_id}",
        description="Sandbox group for training data collection",
        assignment_status=ServiceType.DONE,
        owner_id=collector.id,
    )
    db.add(group)
    await db.flush()

    collection = TrainingCollection(
        project_group_id=group.id,
        dataset_group_id=dataset_group_id,
        created_by_user_id=collector.id,
        status=TrainingCollectionStatus.PROCESSING,
    )
    db.add(collection)
    await db.flush()

    db.add(
        GroupMembership(
            group_id=group.id,
            user_id=collector.id,
            role=GroupMemberRole.INSTRUCTOR,
        )
    )

    student_id_by_user_id: dict[str, str] = {}
    platform_identities: dict[str, PlatformIdentity] = {}
    member_previews: list[IdentityMemberPreview] = []

    for index, row in enumerate(identity_rows, start=1):
        dataset_student_id = str(index)
        synthetic_email = (
            f"training-{collection.id}-{dataset_student_id}@collabtrack.local"
        )
        sandbox_user = User(
            email=synthetic_email,
            name=row.name,
            role=RoleType.STUDENT,
            is_active=True,
        )
        db.add(sandbox_user)
        await db.flush()

        db.add(
            GroupMembership(
                group_id=group.id,
                user_id=sandbox_user.id,
                role=GroupMemberRole.STUDENT,
            )
        )
        db.add(
            TrainingCollectionMember(
                collection_id=collection.id,
                user_id=sandbox_user.id,
                dataset_student_id=dataset_student_id,
                display_name=row.name,
                github_email=row.github_email,
                google_docs_email=row.google_docs_email,
                google_meet_email=row.google_meet_email,
            )
        )
        db.add(
            MeetingNameMapping(
                group_id=group.id,
                display_name=row.name,
                user_id=sandbox_user.id,
            )
        )

        student_id_by_user_id[sandbox_user.id] = dataset_student_id
        platform_identities[sandbox_user.id] = PlatformIdentity(
            github_email=row.github_email,
            google_docs_email=row.google_docs_email,
            google_meet_email=row.google_meet_email,
        )
        member_previews.append(
            IdentityMemberPreview(
                name=row.name,
                github_email=row.github_email,
                google_docs_email=row.google_docs_email,
                google_meet_email=row.google_meet_email,
            )
        )

    warnings: list[str] = []

    try:
        for url in github_urls:
            owner, repo = parse_github_repo_url(url.strip())
            await link_github_repo(group, url.strip(), owner, repo, db)

        for url in google_doc_urls:
            file_id = parse_google_doc_url(url.strip())
            await link_google_doc(group, url.strip(), file_id, db)

        await db.commit()
        await db.refresh(group)
        await db.refresh(collection)

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
                collector,
                db,
            )
            session_out = await upload_meeting_files(
                session,
                attendance_file=att_file,
                transcript_file=trans_file,
                chat_file=chat_file,
                user=collector,
                db=db,
            )
            if session_out.status == MeetingSessionStatus.NEEDS_MAPPING.value:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Meeting files contain names that could not be mapped to roster members.",
                )
            if session_out.status == MeetingSessionStatus.FAILED.value:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=session_out.error_message or "Meeting processing failed.",
                )

        sync_result = await sync_group_participation(
            group,
            db,
            platform_identities=platform_identities,
        )
        warnings.extend(sync_result.warnings)
        await db.commit()

        contributions = await get_contributions(group, db)

        completed_sessions = await db.scalars(
            select(MeetingSession).where(
                MeetingSession.group_id == group.id,
                MeetingSession.status == MeetingSessionStatus.COMPLETED,
            )
        )
        session_ids = [session.id for session in completed_sessions.all()]

        speaking_total = 0
        chat_total = 0
        if session_ids:
            raw_metrics = await db.scalars(
                select(MeetingRawMetric).where(
                    MeetingRawMetric.meeting_session_id.in_(session_ids)
                )
            )
            for metric in raw_metrics.all():
                speaking_total += metric.speaking_turns
                chat_total += metric.chat_messages

        group_activity_totals = build_group_activity_totals(
            contributions.members,
            total_meeting_sessions=len(session_ids),
            total_speaking_turns=speaking_total,
            total_chat_messages=chat_total,
        )

        feature_rows = compute_dataset_features(
            contributions=contributions,
            dataset_group_id=dataset_group_id,
            student_id_by_user_id=student_id_by_user_id,
            group_activity_totals=group_activity_totals,
        )

        dataset_records: list[CollabTrackDataset] = []
        for row in feature_rows:
            record = CollabTrackDataset(
                student_id=row.student_id,
                group_id=row.group_id,
                code_commits=row.code_commits,
                code_share=row.code_share,
                review_participation=row.review_participation,
                attendance_ratio=row.attendance_ratio,
                speaking_participation_ratio=row.speaking_participation_ratio,
                chat_participation_ratio=row.chat_participation_ratio,
                docs_contribution_share=row.docs_contribution_share,
                comment_activity=row.comment_activity,
                benchmark_score=row.benchmark_score,
            )
            db.add(record)
            dataset_records.append(record)

        collection.status = TrainingCollectionStatus.COMPLETED
        collection.completed_at = datetime.now(timezone.utc)
        collection.error_message = None
        await db.commit()

        for record in dataset_records:
            await db.refresh(record)

        return TrainingCollectionDetailOut(
            id=collection.id,
            project_group_id=collection.project_group_id,
            dataset_group_id=collection.dataset_group_id,
            created_by_user_id=collection.created_by_user_id,
            status=collection.status,
            error_message=collection.error_message,
            created_at=collection.created_at,
            completed_at=collection.completed_at,
            members=member_previews,
            dataset_rows=[
                CollabTrackDatasetOut.model_validate(record)
                for record in dataset_records
            ],
            warnings=warnings,
        )
    except HTTPException as exc:
        collection.status = TrainingCollectionStatus.FAILED
        collection.error_message = str(exc.detail)
        collection.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise
    except Exception as exc:
        collection.status = TrainingCollectionStatus.FAILED
        collection.error_message = str(exc)
        collection.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Training data collection failed.",
        ) from exc


async def get_training_collection_detail(
    collection_id: str,
    user_id: str,
    db: AsyncSession,
) -> TrainingCollectionDetailOut:
    collection = await db.scalar(
        select(TrainingCollection)
        .where(
            TrainingCollection.id == collection_id,
            TrainingCollection.created_by_user_id == user_id,
        )
        .options(selectinload(TrainingCollection.members))
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Training collection not found.")

    dataset_rows = await db.scalars(
        select(CollabTrackDataset)
        .where(CollabTrackDataset.group_id == collection.dataset_group_id)
        .order_by(CollabTrackDataset.student_id.asc())
    )

    members = [
        IdentityMemberPreview(
            name=member.display_name,
            github_email=member.github_email,
            google_docs_email=member.google_docs_email,
            google_meet_email=member.google_meet_email,
        )
        for member in collection.members
    ]

    return TrainingCollectionDetailOut(
        id=collection.id,
        project_group_id=collection.project_group_id,
        dataset_group_id=collection.dataset_group_id,
        created_by_user_id=collection.created_by_user_id,
        status=collection.status,
        error_message=collection.error_message,
        created_at=collection.created_at,
        completed_at=collection.completed_at,
        members=members,
        dataset_rows=[
            CollabTrackDatasetOut.model_validate(row) for row in dataset_rows.all()
        ],
        warnings=[],
    )


async def list_training_collections(
    user_id: str, db: AsyncSession
) -> list[TrainingCollection]:
    result = await db.scalars(
        select(TrainingCollection)
        .where(TrainingCollection.created_by_user_id == user_id)
        .order_by(TrainingCollection.created_at.desc())
    )
    return list(result.all())
