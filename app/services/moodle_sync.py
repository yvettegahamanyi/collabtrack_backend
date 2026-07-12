import logging
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password
from app.models import (
    AccountStatus,
    Assignment,
    CourseClass,
    GroupMemberRole,
    GroupMembership,
    MoodleActivityLink,
    MoodleCourseLink,
    MoodleGroupLink,
    ProjectGroup,
    RoleType,
    ServiceType,
    User,
)
from app.services.assignments import allocate_group_number
from app.services.group_members import add_member_if_missing
from app.services.lti.config import (
    LTI_CLAIM_CONTEXT,
    LTI_CLAIM_RESOURCE_LINK,
    LTI_CLAIM_ROLES,
    is_instructor_launch,
)
from app.services.lti.grade_passback import (
    apply_ags_endpoint_to_activity_link,
    extract_ags_endpoint,
)
from app.services.lti.identity import resolve_launch_identity
from app.services.meeting_parser import MemberRow
from app.services.moodle_client import (
    MoodleClientError,
    get_course_groups,
    get_group_enrolled_users,
    moodle_ws_configured,
)
from app.services.report_provisioning import provision_members_from_attendance

logger = logging.getLogger(__name__)


@dataclass
class MoodleLaunchResult:
    access_token: str
    assignment_id: str
    class_id: str
    groups_imported: int
    members_added: int
    warnings: list[str]


async def get_or_create_lti_instructor(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    lti_sub: str,
) -> User:
    normalized_email = email.lower().strip()
    user = await db.scalar(select(User).where(User.moodle_lti_sub == lti_sub))
    if user is None:
        user = await db.scalar(select(User).where(User.email == normalized_email))

    if user is not None:
        if user.role not in (RoleType.INSTRUCTOR, None):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This Moodle account maps to a non-instructor CollabTrack user.",
            )
        if user.role is None:
            user.role = RoleType.INSTRUCTOR
        if name and not user.name:
            user.name = name.strip()
        if not user.moodle_lti_sub:
            user.moodle_lti_sub = lti_sub
        user.account_status = AccountStatus.ACTIVE
        user.is_active = True
        db.add(user)
        await db.flush()
        return user

    user = User(
        email=normalized_email,
        name=name.strip() or normalized_email.split("@")[0],
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role=RoleType.INSTRUCTOR,
        is_active=True,
        account_status=AccountStatus.ACTIVE,
        moodle_lti_sub=lti_sub,
    )
    db.add(user)
    await db.flush()
    return user


async def resolve_or_create_class(
    db: AsyncSession,
    *,
    instructor: User,
    moodle_issuer: str,
    moodle_course_id: str,
    course_title: str,
) -> CourseClass:
    link = await db.scalar(
        select(MoodleCourseLink).where(
            MoodleCourseLink.moodle_issuer == moodle_issuer,
            MoodleCourseLink.moodle_course_id == moodle_course_id,
        )
    )
    if link is not None:
        course_class = await db.get(CourseClass, link.class_id)
        if course_class is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Linked CollabTrack class was not found.",
            )
        if course_class.instructor_id != instructor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This Moodle course is linked to another instructor.",
            )
        if course_title and course_class.name != course_title:
            course_class.name = course_title
            db.add(course_class)
        return course_class

    course_class = CourseClass(
        name=course_title or f"Moodle Course {moodle_course_id}",
        description=f"Imported from Moodle course {moodle_course_id}",
        instructor_id=instructor.id,
    )
    db.add(course_class)
    await db.flush()
    db.add(
        MoodleCourseLink(
            class_id=course_class.id,
            moodle_issuer=moodle_issuer,
            moodle_course_id=moodle_course_id,
        )
    )
    await db.flush()
    return course_class


async def resolve_or_create_assignment(
    db: AsyncSession,
    *,
    course_class: CourseClass,
    moodle_issuer: str,
    moodle_course_id: str,
    moodle_resource_link_id: str,
    activity_title: str,
) -> Assignment:
    link = await db.scalar(
        select(MoodleActivityLink).where(
            MoodleActivityLink.moodle_issuer == moodle_issuer,
            MoodleActivityLink.moodle_resource_link_id == moodle_resource_link_id,
        )
    )
    if link is not None:
        assignment = await db.get(Assignment, link.assignment_id)
        if assignment is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Linked CollabTrack assignment was not found.",
            )
        if assignment.class_id != course_class.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This Moodle activity is linked to a different class.",
            )
        if activity_title and assignment.title != activity_title:
            assignment.title = activity_title
            db.add(assignment)
        return assignment

    assignment = Assignment(
        class_id=course_class.id,
        title=activity_title or f"Moodle Activity {moodle_resource_link_id}",
        description="Imported from Moodle LTI activity.",
        status=ServiceType.ACTIVE,
    )
    db.add(assignment)
    await db.flush()
    db.add(
        MoodleActivityLink(
            assignment_id=assignment.id,
            moodle_issuer=moodle_issuer,
            moodle_course_id=moodle_course_id,
            moodle_resource_link_id=moodle_resource_link_id,
        )
    )
    await db.flush()
    return assignment


async def _ensure_instructor_membership(
    db: AsyncSession,
    *,
    group_id: str,
    instructor_id: str,
) -> None:
    await add_member_if_missing(
        db,
        group_id=group_id,
        user_id=instructor_id,
        role=GroupMemberRole.INSTRUCTOR,
    )


async def _resolve_or_create_moodle_group(
    db: AsyncSession,
    *,
    assignment: Assignment,
    instructor: User,
    moodle_issuer: str,
    moodle_course_id: str,
    moodle_group_id: str,
    group_name: str,
) -> ProjectGroup:
    link = await db.scalar(
        select(MoodleGroupLink).where(
            MoodleGroupLink.moodle_issuer == moodle_issuer,
            MoodleGroupLink.moodle_course_id == moodle_course_id,
            MoodleGroupLink.moodle_group_id == moodle_group_id,
        )
    )
    if link is not None:
        group = await db.get(ProjectGroup, link.group_id)
        if group is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Linked CollabTrack group was not found.",
            )
        if group.assignment_id != assignment.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This Moodle group is linked to a different assignment.",
            )
        if group_name and group.group_name != group_name:
            group.group_name = group_name
            db.add(group)
        await _ensure_instructor_membership(
            db, group_id=group.id, instructor_id=instructor.id
        )
        return group

    group_number = await allocate_group_number(assignment.id, db)
    group = ProjectGroup(
        group_name=group_name or f"Group {group_number}",
        description="Imported from Moodle group.",
        owner_id=instructor.id,
        assignment_id=assignment.id,
        group_number=group_number,
        report_status=None,
    )
    db.add(group)
    await db.flush()
    db.add(
        MoodleGroupLink(
            group_id=group.id,
            moodle_issuer=moodle_issuer,
            moodle_course_id=moodle_course_id,
            moodle_group_id=moodle_group_id,
        )
    )
    await _ensure_instructor_membership(
        db, group_id=group.id, instructor_id=instructor.id
    )
    await db.flush()
    return group


def _member_rows_from_moodle_users(users: list[dict]) -> list[MemberRow]:
    rows: list[MemberRow] = []
    for user in users:
        email = (user.get("email") or "").strip()
        if not email:
            continue
        fullname = (user.get("fullname") or "").strip()
        if not fullname:
            first = (user.get("firstname") or "").strip()
            last = (user.get("lastname") or "").strip()
            fullname = f"{first} {last}".strip() or email.split("@")[0]
        rows.append(
            MemberRow(
                name=fullname,
                email=email,
                moodle_user_id=str(user.get("id", "")).strip() or None,
            )
        )
    return rows


async def import_moodle_groups_for_assignment(
    db: AsyncSession,
    *,
    assignment: Assignment,
    instructor: User,
    moodle_issuer: str,
    moodle_course_id: str,
) -> tuple[int, int, list[str]]:
    warnings: list[str] = []
    if not moodle_ws_configured():
        warnings.append(
            "Moodle web services are not configured; groups were not imported. "
            "Set MOODLE_BASE_URL and MOODLE_WS_TOKEN."
        )
        return 0, 0, warnings

    try:
        course_id_int = int(moodle_course_id)
    except ValueError:
        warnings.append(f"Invalid Moodle course id '{moodle_course_id}'.")
        return 0, 0, warnings

    try:
        moodle_groups = await get_course_groups(course_id_int)
    except MoodleClientError as exc:
        warnings.append(f"Failed to fetch Moodle groups: {exc}")
        return 0, 0, warnings

    if not moodle_groups:
        warnings.append("No Moodle groups found for this course.")
        return 0, 0, warnings

    groups_imported = 0
    members_added = 0

    for moodle_group in moodle_groups:
        moodle_group_id = str(moodle_group.get("id", ""))
        if not moodle_group_id:
            continue
        group_name = (moodle_group.get("name") or f"Group {moodle_group_id}").strip()

        group = await _resolve_or_create_moodle_group(
            db,
            assignment=assignment,
            instructor=instructor,
            moodle_issuer=moodle_issuer,
            moodle_course_id=moodle_course_id,
            moodle_group_id=moodle_group_id,
            group_name=group_name,
        )
        groups_imported += 1

        try:
            enrolled = await get_group_enrolled_users(course_id_int, int(moodle_group_id))
        except MoodleClientError as exc:
            warnings.append(
                f"Could not fetch members for Moodle group '{group_name}': {exc}"
            )
            continue

        member_rows = _member_rows_from_moodle_users(enrolled)
        if not member_rows:
            warnings.append(
                f"Moodle group '{group_name}' has no members with email addresses."
            )
            continue

        added = await provision_members_from_attendance(
            db,
            group_id=group.id,
            instructor_id=instructor.id,
            rows=member_rows,
        )
        members_added += added

    return groups_imported, members_added, warnings


async def handle_instructor_lti_launch(
    db: AsyncSession,
    launch_data: dict,
) -> MoodleLaunchResult:
    roles = launch_data.get(LTI_CLAIM_ROLES, [])
    if not is_instructor_launch(roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can launch CollabTrack from Moodle.",
        )

    email, name = await resolve_launch_identity(launch_data)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Moodle did not provide an email address in the LTI launch. "
                "In Moodle tool settings set Privacy → "
                "'Share launcher's email with tool' to Always, and ensure your "
                "Moodle profile has an email address. CollabTrack can also look up "
                "the email via web services when core_user_get_users_by_field is "
                "enabled on your token."
            ),
        )

    lti_sub = str(launch_data.get("sub", ""))
    if not lti_sub:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing LTI subject identifier.",
        )

    moodle_issuer = str(launch_data.get("iss", "")).rstrip("/")
    context = launch_data.get(LTI_CLAIM_CONTEXT) or {}
    resource_link = launch_data.get(LTI_CLAIM_RESOURCE_LINK) or {}

    moodle_course_id = str(context.get("id", "")).strip()
    if not moodle_course_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Moodle course context was not included in the LTI launch.",
        )

    moodle_resource_link_id = str(resource_link.get("id", "")).strip()
    if not moodle_resource_link_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Moodle resource link id was not included in the LTI launch.",
        )

    course_title = (
        context.get("title")
        or context.get("label")
        or f"Moodle Course {moodle_course_id}"
    )
    activity_title = (
        resource_link.get("title")
        or resource_link.get("description")
        or f"CollabTrack Activity {moodle_resource_link_id}"
    )

    instructor = await get_or_create_lti_instructor(
        db,
        email=email,
        name=name,
        lti_sub=lti_sub,
    )
    course_class = await resolve_or_create_class(
        db,
        instructor=instructor,
        moodle_issuer=moodle_issuer,
        moodle_course_id=moodle_course_id,
        course_title=str(course_title),
    )
    assignment = await resolve_or_create_assignment(
        db,
        course_class=course_class,
        moodle_issuer=moodle_issuer,
        moodle_course_id=moodle_course_id,
        moodle_resource_link_id=moodle_resource_link_id,
        activity_title=str(activity_title),
    )

    activity_link = await db.scalar(
        select(MoodleActivityLink).where(
            MoodleActivityLink.assignment_id == assignment.id
        )
    )
    ags_endpoint = extract_ags_endpoint(launch_data)
    if activity_link is not None and ags_endpoint is not None:
        apply_ags_endpoint_to_activity_link(activity_link, ags_endpoint)
        db.add(activity_link)
        logger.info(
            "Stored Moodle AGS endpoints for assignment %s (lineitem=%s scopes=%s)",
            assignment.id,
            bool(activity_link.ags_lineitem_url),
            activity_link.ags_scopes,
        )

    groups_imported, members_added, warnings = await import_moodle_groups_for_assignment(
        db,
        assignment=assignment,
        instructor=instructor,
        moodle_issuer=moodle_issuer,
        moodle_course_id=moodle_course_id,
    )

    if activity_link is not None and ags_endpoint is None:
        warnings.append(
            "Moodle did not include grade passback (AGS) endpoints in this launch. "
            "Enable Assignment and Grade Services on the external tool, then launch "
            "CollabTrack again to sync scores to the Moodle gradebook."
        )

    await db.commit()

    access_token = create_access_token(subject=instructor.id)
    logger.info(
        "Moodle LTI launch: instructor=%s assignment=%s groups=%s members=%s",
        instructor.email,
        assignment.id,
        groups_imported,
        members_added,
    )
    return MoodleLaunchResult(
        access_token=access_token,
        assignment_id=assignment.id,
        class_id=course_class.id,
        groups_imported=groups_imported,
        members_added=members_added,
        warnings=warnings,
    )
