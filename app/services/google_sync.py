from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.schemas.participation import GoogleDocsMetrics

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_ACTIVITY_API = "https://driveactivity.googleapis.com/v2/activity:query"

def _parse_google_api_error(resp: httpx.Response) -> dict[str, str | int | None]:
    """Extract structured error fields from a Google API error response."""
    result: dict[str, str | int | None] = {
        "http_status": resp.status_code,
        "message": resp.text or f"HTTP {resp.status_code}",
        "reason": None,
        "error_status": None,
    }
    try:
        payload = resp.json()
        error = payload.get("error") or {}
        result["message"] = error.get("message") or result["message"]
        result["error_status"] = error.get("status")
        errors = error.get("errors") or []
        if errors:
            result["reason"] = errors[0].get("reason")
    except ValueError:
        pass
    return result


def _classify_drive_sync_failure(error: dict[str, str | int | None]) -> str:
    reason = str(error.get("reason") or "")
    message = str(error.get("message") or "").lower()
    if reason == "accessNotConfigured" or "has not been used in project" in message:
        return "drive_api_disabled"
    if "drive activity" in message or "driveactivity" in message:
        if "has not been used" in message or "disabled" in message:
            return "drive_api_disabled"
    if reason in {"insufficientPermissions", "forbidden"} or "insufficient" in message:
        return "insufficient_scope_or_access"
    return "unknown"


@dataclass
class GoogleDocSyncStatus:
    file_id: str
    revisions_status: int | None = None
    comments_status: int | None = None
    activity_status: int | None = None
    revision_count: int = 0
    comment_count: int = 0
    activity_edit_count: int = 0
    activity_comment_count: int = 0
    matched_emails: list[str] = field(default_factory=list)
    error: str | None = None
    failure_kind: str | None = None
    metadata_status: int | None = None
    activity_source: str | None = None  # activity | revisions | none
    activity_scope_granted: bool = False
    people_lookup_scope_granted: bool = False


@dataclass
class GoogleDocEvent:
    type: str
    file_id: str
    source_id: str | None
    author_email: str | None
    author_name: str | None
    matched_email: str | None
    match_method: str | None
    timestamp: str | None = None

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "file_id": self.file_id,
            "source_id": self.source_id,
            "author_email": self.author_email,
            "author_name": self.author_name,
            "matched_email": self.matched_email,
            "match_method": self.match_method,
            "timestamp": self.timestamp,
        }


@dataclass
class GoogleSyncResult:
    by_email: dict[str, GoogleDocsMetrics] = field(default_factory=dict)
    events_by_email: dict[str, list[GoogleDocEvent]] = field(default_factory=dict)

    def get_or_create(self, email: str) -> GoogleDocsMetrics:
        key = email.lower()
        if key not in self.by_email:
            self.by_email[key] = GoogleDocsMetrics()
        return self.by_email[key]

    def record_event(self, matched_email: str, event: GoogleDocEvent) -> None:
        key = matched_email.lower()
        self.events_by_email.setdefault(key, []).append(event)


def _normalize_person_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _build_author_lookup(
    member_emails: set[str],
    name_to_email: dict[str, str] | None,
    permission_map: dict[str, str],
) -> dict[str, str]:
    lookup: dict[str, str] = dict(permission_map)
    if name_to_email:
        lookup.update(name_to_email)
    for email in member_emails:
        local = email.split("@", 1)[0].lower()
        lookup[local] = email
        lookup[_normalize_person_name(local)] = email
    return lookup


def _resolve_author_email(
    author: dict,
    token_holder_email: str | None,
    name_to_email: dict[str, str] | None = None,
    member_emails: set[str] | None = None,
) -> tuple[str | None, str | None]:
    """Return (resolved email, match_method) from a Drive User object."""
    email = (author.get("emailAddress") or "").strip().lower()
    if email:
        return email, "email"
    if author.get("me") and token_holder_email:
        return token_holder_email.lower(), "me"
    display_name = author.get("displayName")
    if display_name:
        normalized = _normalize_person_name(display_name)
        local_key = display_name.strip().lower()
        if name_to_email:
            matched = name_to_email.get(normalized) or name_to_email.get(local_key)
            if matched:
                return matched.lower(), "display_name"
        if member_emails:
            for member_email in member_emails:
                if member_email.split("@", 1)[0] == local_key:
                    return member_email, "member_local_part"
    return None, None


def _canonicalize_member_email(
    resolved_email: str | None,
    signup_emails: set[str],
    email_aliases: set[str],
    email_canonical: dict[str, str],
) -> str | None:
    """Map a resolved Google identity to a CollabTrack signup email for storage."""
    if not resolved_email:
        return None
    lower = resolved_email.lower()
    if lower in email_canonical:
        return email_canonical[lower]
    if lower in signup_emails:
        return lower
    local = lower.split("@", 1)[0]
    for alias in email_aliases:
        if alias.split("@", 1)[0] == local:
            return email_canonical.get(alias, alias)
    return None


def _extend_email_aliases_from_permissions(
    permission_map: dict[str, str],
    author_lookup: dict[str, str],
    signup_emails: set[str],
    email_canonical: dict[str, str],
) -> set[str]:
    """Add file-permission emails that map to group members via name/local-part."""
    aliases = set(email_canonical.keys())
    for key, perm_email in permission_map.items():
        canonical = author_lookup.get(key)
        if canonical and canonical in signup_emails:
            perm_lower = perm_email.lower()
            email_canonical[perm_lower] = canonical
            aliases.add(perm_lower)
    return aliases


def _attribute_to_member(
    *,
    result: GoogleSyncResult,
    signup_emails: set[str],
    email_aliases: set[str],
    email_canonical: dict[str, str],
    author: dict,
    token_holder_email: str | None,
    name_to_email: dict[str, str] | None,
    file_id: str,
    source_id: str | None,
    event_type: str,
    timestamp: str | None,
    metric_field: str,
) -> str | None:
    resolved_email, match_method = _resolve_author_email(
        author,
        token_holder_email,
        name_to_email,
        member_emails=email_aliases,
    )
    author_name = author.get("displayName")
    storage_email = _canonicalize_member_email(
        resolved_email, signup_emails, email_aliases, email_canonical
    )
    if storage_email:
        metrics = result.get_or_create(storage_email)
        current = getattr(metrics, metric_field)
        setattr(metrics, metric_field, current + 1)
        result.record_event(
            storage_email,
            GoogleDocEvent(
                type=event_type,
                file_id=file_id,
                source_id=source_id,
                author_email=resolved_email,
                author_name=author_name,
                matched_email=storage_email,
                match_method=match_method,
                timestamp=timestamp,
            ),
        )
    return resolved_email


def _merge_name_maps(
    *maps: dict[str, str] | None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    for name_map in maps:
        if name_map:
            merged.update(name_map)
    return merged


async def _fetch_permission_name_map(
    client: httpx.AsyncClient,
    file_id: str,
    headers: dict[str, str],
) -> dict[str, str]:
    """Build lookup keys (display name, email local-part) -> email from file ACL."""
    name_to_email: dict[str, str] = {}
    page_token: str | None = None
    while True:
        params: dict = {
            "fields": "permissions(emailAddress,displayName),nextPageToken",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        resp = await client.get(
            f"{_DRIVE_API}/files/{file_id}/permissions",
            headers=headers,
            params=params,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        for perm in data.get("permissions") or []:
            email = (perm.get("emailAddress") or "").strip().lower()
            display = perm.get("displayName")
            if not email:
                continue
            if display:
                name_to_email[_normalize_person_name(display)] = email
                local = email.split("@", 1)[0]
                name_to_email[local.lower()] = email
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return name_to_email


async def _token_has_activity_scope(
    client: httpx.AsyncClient,
    access_token: str,
) -> bool:
    resp = await client.get(
        "https://www.googleapis.com/oauth2/v1/tokeninfo",
        params={"access_token": access_token},
    )
    if resp.status_code != 200:
        return False
    scope = str(resp.json().get("scope") or "")
    return "drive.activity.readonly" in scope


async def _token_has_people_lookup_scope(
    client: httpx.AsyncClient,
    access_token: str,
) -> bool:
    resp = await client.get(
        "https://www.googleapis.com/oauth2/v1/tokeninfo",
        params={"access_token": access_token},
    )
    if resp.status_code != 200:
        return False
    scope = str(resp.json().get("scope") or "")
    return (
        "contacts.readonly" in scope
        or "directory.readonly" in scope
    )


_DIRECTORY_SOURCES = (
    "DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT,"
    "DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"
)


def _collect_unknown_person_edit_counts(
    activities: list[dict],
    person_to_canonical: dict[str, str],
) -> dict[str, int]:
    """Count EDIT activities per Activity API personName not yet mapped."""
    counts: dict[str, int] = {}
    for activity in activities:
        if not _activity_has_edit(activity):
            continue
        actions = activity.get("actions") or []
        actor = _activity_edit_actor(activity, actions)
        known = (actor.get("user") or {}).get("knownUser") or {}
        person_name = known.get("personName")
        if person_name and person_name not in person_to_canonical:
            counts[person_name] = counts.get(person_name, 0) + 1
    return counts


def _infer_person_map_for_unmatched_members(
    person_to_canonical: dict[str, str],
    activities: list[dict],
    signup_emails: set[str],
) -> dict[str, str]:
    """
    When directory lookup cannot resolve person IDs, infer mapping for domain-wide
    editors who were never individually shared on the file.
    Safe when exactly one group member and one unknown Activity person remain.
    """
    mapped_canonicals = set(person_to_canonical.values())
    unmatched_members = sorted(e for e in signup_emails if e not in mapped_canonicals)
    unknown_edits = _collect_unknown_person_edit_counts(
        activities, person_to_canonical
    )
    if not unmatched_members or not unknown_edits:
        return {}
    if len(unmatched_members) == 1 and len(unknown_edits) == 1:
        person_id = next(iter(unknown_edits))
        return {person_id: unmatched_members[0]}
    return {}


async def _build_directory_person_map_from_members(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    signup_emails: set[str],
    email_aliases: set[str],
    email_canonical: dict[str, str],
    author_lookup: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map domain directory person resourceNames to CollabTrack signup emails."""
    person_map: dict[str, str] = {}
    queries: set[str] = set()
    for email in signup_emails:
        queries.add(email)
        queries.add(email.split("@", 1)[0])
    if author_lookup:
        for key in author_lookup:
            if " " in key or "." in key:
                queries.add(key)

    for query in sorted(queries):
        if not query.strip():
            continue
        resp = await client.get(
            "https://people.googleapis.com/v1/people:searchDirectoryPeople",
            headers=headers,
            params={
                "query": query,
                "readMask": "emailAddresses,names",
                "sources": _DIRECTORY_SOURCES,
                "pageSize": 10,
            },
        )
        if resp.status_code != 200:
            continue
        payload = resp.json()
        people = payload.get("people") or []
        if not people:
            continue
        for person in people:
            resource_name = person.get("resourceName")
            if not resource_name:
                continue
            matched_canonical: str | None = None
            directory_emails: list[str] = []
            for entry in person.get("emailAddresses") or []:
                resolved = (entry.get("value") or "").strip().lower()
                if not resolved:
                    continue
                directory_emails.append(resolved)
                canonical = _canonicalize_member_email(
                    resolved, signup_emails, email_aliases, email_canonical
                )
                if canonical:
                    matched_canonical = canonical
                    break
            if not matched_canonical and author_lookup:
                for name_entry in person.get("names") or []:
                    display = (
                        name_entry.get("displayName")
                        or name_entry.get("unstructuredName")
                        or ""
                    ).strip()
                    if not display:
                        continue
                    matched_canonical = (
                        author_lookup.get(_normalize_person_name(display))
                        or author_lookup.get(display.lower())
                    )
                    if matched_canonical:
                        break
            if matched_canonical:
                person_map[resource_name] = matched_canonical
    return person_map


async def _resolve_person_email(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    person_name: str,
    cache: dict[str, str | None],
) -> str | None:
    if person_name in cache:
        return cache[person_name]
    resp = await client.get(
        f"https://people.googleapis.com/v1/{person_name}",
        headers=headers,
        params={"personFields": "emailAddresses,names"},
    )
    if resp.status_code != 200:
        cache[person_name] = None
        return None
    data = resp.json()
    for entry in data.get("emailAddresses") or []:
        email = (entry.get("value") or "").strip().lower()
        if email:
            cache[person_name] = email
            return email
    cache[person_name] = None
    return None


async def _enrich_person_map_via_people_api(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    activities: list[dict],
    person_map: dict[str, str],
    signup_emails: set[str],
    email_aliases: set[str],
    email_canonical: dict[str, str],
) -> dict[str, str | None]:
    """Resolve unknown Activity actor personNames to group member emails via People API."""
    person_cache: dict[str, str | None] = {}
    unique_persons: set[str] = set()
    for activity in activities:
        if not _activity_has_edit(activity):
            continue
        actions = activity.get("actions") or []
        actor = _activity_edit_actor(activity, actions)
        known = (actor.get("user") or {}).get("knownUser") or {}
        person_name = known.get("personName")
        if person_name and person_name not in person_map:
            unique_persons.add(person_name)
        for actor_entry in activity.get("actors") or []:
            entry_known = (actor_entry.get("user") or {}).get("knownUser") or {}
            entry_person = entry_known.get("personName")
            if entry_person and entry_person not in person_map:
                unique_persons.add(entry_person)

    for person_name in unique_persons:
        email = await _resolve_person_email(
            client, headers, person_name, person_cache
        )
        if email:
            canonical = _canonicalize_member_email(
                email, signup_emails, email_aliases, email_canonical
            )
            if canonical:
                person_map[person_name] = canonical
    return person_cache


def _owner_person_map_from_activities(
    activities: list[dict],
    owner_canonical_email: str | None,
) -> dict[str, str]:
    """Map Drive Activity owner personName to the file owner's signup email."""
    if not owner_canonical_email:
        return {}
    person_map: dict[str, str] = {}
    for activity in activities:
        for target in activity.get("targets") or []:
            drive_item = target.get("driveItem") or {}
            owner = drive_item.get("owner") or {}
            user = owner.get("user") or {}
            known = user.get("knownUser") or {}
            person_name = known.get("personName")
            if person_name:
                person_map[person_name] = owner_canonical_email
    return person_map


def _build_activity_person_map(
    activities: list[dict],
    owner_canonical_email: str | None,
    token_holder_email: str | None,
    email_canonical: dict[str, str],
) -> dict[str, str]:
    """Map Activity API personName IDs to CollabTrack signup emails without People API."""
    person_map = _owner_person_map_from_activities(activities, owner_canonical_email)
    if not token_holder_email:
        return person_map
    holder = email_canonical.get(
        token_holder_email.lower(), token_holder_email.lower()
    )
    for activity in activities:
        if not _activity_has_edit(activity):
            continue
        actions = activity.get("actions") or []
        actor = _activity_edit_actor(activity, actions)
        known = (actor.get("user") or {}).get("knownUser") or {}
        if known.get("isCurrentUser"):
            person_name = known.get("personName")
            if person_name:
                person_map[person_name] = holder
        for actor_entry in activity.get("actors") or []:
            entry_known = (actor_entry.get("user") or {}).get("knownUser") or {}
            if entry_known.get("isCurrentUser"):
                person_name = entry_known.get("personName")
                if person_name:
                    person_map[person_name] = holder
    return person_map


async def _resolve_activity_actor_email(
    *,
    actor: dict,
    token_holder_email: str | None,
    person_to_canonical: dict[str, str],
) -> tuple[str | None, str | None]:
    user = actor.get("user") or {}
    if user.get("unknownUser"):
        return None, "anonymous"
    known = user.get("knownUser")
    if known:
        if known.get("isCurrentUser") and token_holder_email:
            return token_holder_email.lower(), "me"
        person_name = known.get("personName")
        if person_name and person_name in person_to_canonical:
            return person_to_canonical[person_name], "activity_person_map"
    return None, None


def _activity_timestamp(activity: dict, action: dict | None = None) -> str | None:
    if action:
        if action.get("timestamp"):
            return str(action["timestamp"])
        time_range = action.get("timeRange") or {}
        end = time_range.get("endTime") or time_range.get("startTime")
        if end and end.get("seconds"):
            return str(end["seconds"])
    if activity.get("timestamp"):
        return str(activity["timestamp"])
    time_range = activity.get("timeRange") or {}
    end = time_range.get("endTime") or time_range.get("startTime")
    if end and end.get("seconds"):
        return str(end["seconds"])
    return None


def _is_edit_detail(detail: dict) -> bool:
    """True only for edit actions; ignore create, rename, move, share, etc."""
    return detail.get("edit") is not None


def _activity_has_edit(activity: dict) -> bool:
    actions = activity.get("actions") or []
    if actions:
        return any(_is_edit_detail(action.get("detail") or {}) for action in actions)
    primary = activity.get("primaryActionDetail") or {}
    return _is_edit_detail(primary)


def _activity_edit_actor(activity: dict, actions: list[dict]) -> dict:
    """Prefer activity-level actors[0]; fall back to per-action actor."""
    actors = activity.get("actors") or []
    if actors:
        return actors[0]
    for action in actions:
        actor = action.get("actor")
        if actor:
            return actor
    return {}


async def _sync_drive_activity(
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    file_id: str,
    signup_emails: set[str],
    email_aliases: set[str],
    email_canonical: dict[str, str],
    author_lookup: dict[str, str],
    owner_canonical_email: str | None,
    token_holder_email: str | None,
    people_lookup_scope_granted: bool = False,
) -> tuple[GoogleSyncResult, int | None, str | None, dict | None, list[dict], int]:
    """Query Drive Activity API v2 for EDIT actions only (one count per activity)."""
    result = GoogleSyncResult()
    raw_pages: list[dict] = []
    status_code: int | None = None
    error_message: str | None = None
    error_details: dict | None = None
    edit_count = 0
    unattributed_edits = 0
    all_activities: list[dict] = []
    page_token: str | None = None

    while True:
        body: dict = {
            "itemName": f"items/{file_id}",
            "filter": "detail.action_detail_case:EDIT",
            "pageSize": 100,
            "consolidationStrategy": {"none": {}},
        }
        if page_token:
            body["pageToken"] = page_token
        resp = await client.post(_ACTIVITY_API, headers=headers, json=body)
        status_code = resp.status_code
        if resp.status_code != 200:
            error_details = _parse_google_api_error(resp)
            error_message = str(error_details.get("message"))
            try:
                raw_pages.append(resp.json())
            except ValueError:
                raw_pages.append({"raw_text": resp.text[:2000]})
            break
        data = resp.json()
        raw_pages.append(data)
        all_activities.extend(data.get("activities") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    person_to_canonical = _build_activity_person_map(
        all_activities,
        owner_canonical_email,
        token_holder_email,
        email_canonical,
    )
    people_lookup_cache: dict[str, str | None] = {}
    if people_lookup_scope_granted:
        directory_person_map = await _build_directory_person_map_from_members(
            client,
            headers,
            signup_emails,
            email_aliases,
            email_canonical,
            author_lookup,
        )
        person_to_canonical.update(directory_person_map)
        people_lookup_cache = await _enrich_person_map_via_people_api(
            client,
            headers,
            all_activities,
            person_to_canonical,
            signup_emails,
            email_aliases,
            email_canonical,
        )
    inferred_person_map = _infer_person_map_for_unmatched_members(
        person_to_canonical,
        all_activities,
        signup_emails,
    )
    if inferred_person_map:
        person_to_canonical.update(inferred_person_map)


    for activity in all_activities:
        if not _activity_has_edit(activity):
            continue

        actions = activity.get("actions") or []
        edit_count += 1
        actor = _activity_edit_actor(activity, actions)
        resolved_email, match_method = await _resolve_activity_actor_email(
            actor=actor,
            token_holder_email=token_holder_email,
            person_to_canonical=person_to_canonical,
        )
        storage_email = _canonicalize_member_email(
            resolved_email, signup_emails, email_aliases, email_canonical
        )
        if not storage_email:
            unattributed_edits += 1
        action_for_time = actions[0] if actions else None
        timestamp = _activity_timestamp(activity, action_for_time)
        source_id = f"activity:{timestamp}:{edit_count}:edit"
        if storage_email:
            metrics = result.get_or_create(storage_email)
            metrics.edits += 1
            result.record_event(
                storage_email,
                GoogleDocEvent(
                    type="edit",
                    file_id=file_id,
                    source_id=source_id,
                    author_email=resolved_email,
                    author_name=None,
                    matched_email=storage_email,
                    match_method=match_method or "activity",
                    timestamp=timestamp,
                ),
            )


    return (
        result,
        status_code,
        error_message,
        error_details,
        raw_pages,
        edit_count,
    )


async def sync_google_doc(
    *,
    access_token: str,
    file_id: str,
    signup_emails: set[str],
    email_canonical: dict[str, str],
    token_holder_email: str | None = None,
    name_to_email: dict[str, str] | None = None,
) -> tuple[GoogleSyncResult, GoogleDocSyncStatus]:
    headers = {"Authorization": f"Bearer {access_token}"}
    status = GoogleDocSyncStatus(file_id=file_id)
    signup_emails = {email.lower() for email in signup_emails}
    email_canonical = {alias.lower(): canonical.lower() for alias, canonical in email_canonical.items()}
    revision_author_emails: set[str] = set()
    comment_author_emails: set[str] = set()

    async with httpx.AsyncClient(timeout=60.0) as client:
        status.activity_scope_granted = await _token_has_activity_scope(
            client, access_token
        )
        status.people_lookup_scope_granted = await _token_has_people_lookup_scope(
            client, access_token
        )
        permission_map = await _fetch_permission_name_map(client, file_id, headers)
        author_lookup = _build_author_lookup(signup_emails, name_to_email, permission_map)
        email_aliases = _extend_email_aliases_from_permissions(
            permission_map, author_lookup, signup_emails, email_canonical
        )

        metadata_resp = await client.get(
            f"{_DRIVE_API}/files/{file_id}",
            headers=headers,
            params={"fields": "id,name,owners(emailAddress)"},
        )
        status.metadata_status = metadata_resp.status_code
        metadata_error = (
            _parse_google_api_error(metadata_resp)
            if metadata_resp.status_code != 200
            else None
        )
        owner_canonical_email: str | None = None
        if metadata_resp.status_code == 200:
            owners = metadata_resp.json().get("owners") or []
            if owners:
                owner_email = (owners[0].get("emailAddress") or "").strip().lower()
                if owner_email:
                    owner_canonical_email = email_canonical.get(
                        owner_email, owner_email
                    )

        (
            activity_result,
            activity_status,
            activity_error,
            activity_details,
            activity_raw_pages,
            activity_edit_count,
        ) = await _sync_drive_activity(
            client=client,
            headers=headers,
            file_id=file_id,
            signup_emails=signup_emails,
            email_aliases=email_aliases,
            email_canonical=email_canonical,
            author_lookup=author_lookup,
            owner_canonical_email=owner_canonical_email,
            token_holder_email=token_holder_email,
            people_lookup_scope_granted=status.people_lookup_scope_granted,
        )
        status.activity_status = activity_status
        status.activity_edit_count = activity_edit_count
        activity_edit_match_score = sum(
            m.edits for m in activity_result.by_email.values()
        )

        revisions_result = GoogleSyncResult()
        revisions, revisions_status, revisions_error, revisions_details, revisions_raw_pages = await _paginate(
            client,
            f"{_DRIVE_API}/files/{file_id}/revisions",
            headers,
            params={
                "pageSize": 100,
                "fields": (
                    "revisions(id,modifiedTime,lastModifyingUser"
                    "(emailAddress,displayName,me)),nextPageToken"
                ),
            },
            capture_raw_pages=True,
        )
        status.revisions_status = revisions_status
        revision_author_debug: list[dict] = []
        for revision in revisions:
            user = revision.get("lastModifyingUser") or {}
            resolved = _attribute_to_member(
                result=revisions_result,
                signup_emails=signup_emails,
                email_aliases=email_aliases,
                email_canonical=email_canonical,
                author=user,
                token_holder_email=token_holder_email,
                name_to_email=author_lookup,
                file_id=file_id,
                source_id=revision.get("id"),
                event_type="edit",
                timestamp=revision.get("modifiedTime"),
                metric_field="edits",
            )
            revision_author_debug.append(
                {
                    "revision_id": revision.get("id"),
                    "author_keys": sorted(user.keys()),
                    "has_email": bool(user.get("emailAddress")),
                    "me": user.get("me"),
                    "display_name": user.get("displayName"),
                    "resolved_email": resolved,
                }
            )
            if resolved:
                revision_author_emails.add(resolved)

        comments_result = GoogleSyncResult()
        comments, comments_status, comments_error, comments_details, comments_raw_pages = await _paginate_comments(
            client, file_id, headers, capture_raw_pages=True
        )
        status.comments_status = comments_status
        comment_author_debug: list[dict] = []
        reply_count = 0
        for comment in comments:
            author = comment.get("author") or {}
            resolved = _attribute_to_member(
                result=comments_result,
                signup_emails=signup_emails,
                email_aliases=email_aliases,
                email_canonical=email_canonical,
                author=author,
                token_holder_email=token_holder_email,
                name_to_email=author_lookup,
                file_id=file_id,
                source_id=comment.get("id"),
                event_type="comment",
                timestamp=comment.get("createdTime"),
                metric_field="comments",
            )
            comment_author_debug.append(
                {
                    "comment_id": comment.get("id"),
                    "author_keys": sorted(author.keys()),
                    "has_email": bool(author.get("emailAddress")),
                    "me": author.get("me"),
                    "display_name": author.get("displayName"),
                    "resolved_email": resolved,
                }
            )
            if resolved:
                comment_author_emails.add(resolved)
            for reply in comment.get("replies") or []:
                reply_count += 1
                reply_author = reply.get("author") or {}
                reply_resolved = _attribute_to_member(
                    result=comments_result,
                    signup_emails=signup_emails,
                    email_aliases=email_aliases,
                    email_canonical=email_canonical,
                    author=reply_author,
                    token_holder_email=token_holder_email,
                    name_to_email=author_lookup,
                    file_id=file_id,
                    source_id=reply.get("id"),
                    event_type="comment_reply",
                    timestamp=reply.get("createdTime"),
                    metric_field="comments",
                )
                if reply_resolved:
                    comment_author_emails.add(reply_resolved)

        docs_api_resp = await client.get(
            f"https://docs.googleapis.com/v1/documents/{file_id}",
            headers=headers,
            params={"fields": "documentId,title,revisionId"},
        )
        docs_api_body: dict | str
        try:
            docs_api_body = docs_api_resp.json()
        except ValueError:
            docs_api_body = docs_api_resp.text[:2000]

        metadata_full_resp = await client.get(
            f"{_DRIVE_API}/files/{file_id}",
            headers=headers,
            params={
                "fields": (
                    "id,name,mimeType,modifiedTime,createdTime,owners,"
                    "lastModifyingUser,version,headRevisionId,capabilities"
                ),
            },
        )
        metadata_full_body: dict | str
        try:
            metadata_full_body = metadata_full_resp.json()
        except ValueError:
            metadata_full_body = metadata_full_resp.text[:2000]

        revision_edit_match_score = sum(
            m.edits for m in revisions_result.by_email.values()
        )
        result = GoogleSyncResult()
        use_activity_edits = (
            activity_status == 200
            and (
                activity_edit_match_score > 0
                or activity_edit_count > 0
                or revision_edit_match_score == 0
            )
        )
        if use_activity_edits:
            merge_google_results(result, activity_result)
            status.activity_source = "activity"
            for member_email in signup_emails:
                activity_edits = result.by_email.get(member_email)
                rev_metrics = revisions_result.by_email.get(member_email)
                if (
                    (not activity_edits or activity_edits.edits == 0)
                    and rev_metrics
                    and rev_metrics.edits > 0
                ):
                    result.get_or_create(member_email).edits += rev_metrics.edits
        else:
            merge_google_results(result, revisions_result)
            status.activity_source = "revisions"
        # Comments always from Drive API v3 comments endpoint (never Activity API).
        merge_google_results(result, comments_result)

        status.revision_count = len(revisions)
        status.comment_count = len(comments) + reply_count
        status.matched_emails = list(result.by_email.keys())

        if activity_status != 200 and activity_details:
            activity_failure = _classify_drive_sync_failure(activity_details)
            if activity_failure == "drive_api_disabled":
                status.failure_kind = activity_failure
                status.error = activity_error
            elif activity_failure == "insufficient_scope_or_access" and not status.failure_kind:
                status.failure_kind = activity_failure
                status.error = activity_error

        if revisions_status != 200 and comments_status != 200 and activity_status != 200:
            primary_error = revisions_details or comments_details or {}
            status.error = revisions_error or comments_error or (
                f"Drive API returned {revisions_status} for revisions and "
                f"{comments_status} for comments."
            )
            status.failure_kind = _classify_drive_sync_failure(primary_error)
        elif revisions_status != 200:
            status.error = revisions_error or (
                f"Drive API returned {revisions_status} for revisions."
            )
            status.failure_kind = _classify_drive_sync_failure(
                revisions_details or {}
            )



    return result, status


def merge_google_results(
    target: GoogleSyncResult, source: GoogleSyncResult
) -> GoogleSyncResult:
    for email, metrics in source.by_email.items():
        existing = target.get_or_create(email)
        existing.edits += metrics.edits
        existing.comments += metrics.comments
    for email, events in source.events_by_email.items():
        target.events_by_email.setdefault(email, []).extend(events)
    return target


async def fetch_google_doc_title(access_token: str, file_id: str) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_DRIVE_API}/files/{file_id}",
            headers=headers,
            params={"fields": "name"},
        )
        if resp.status_code != 200:
            return "Untitled Document"
        return resp.json().get("name") or "Untitled Document"


async def _paginate(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict | None = None,
    capture_raw_pages: bool = False,
) -> tuple[
    list[dict],
    int | None,
    str | None,
    dict[str, str | int | None] | None,
    list[dict],
]:
    items: list[dict] = []
    raw_pages: list[dict] = []
    page_token: str | None = None
    status_code: int | None = None
    error_message: str | None = None
    error_details: dict[str, str | int | None] | None = None
    while True:
        query = dict(params or {})
        if page_token:
            query["pageToken"] = page_token
        resp = await client.get(url, headers=headers, params=query)
        status_code = resp.status_code
        if resp.status_code != 200:
            error_details = _parse_google_api_error(resp)
            error_message = str(error_details.get("message"))
            if capture_raw_pages:
                try:
                    raw_pages.append(resp.json())
                except ValueError:
                    raw_pages.append({"raw_text": resp.text[:2000]})
            break
        data = resp.json()
        if capture_raw_pages:
            raw_pages.append(data)
        items.extend(data.get("revisions") or data.get("files") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items, status_code, error_message, error_details, raw_pages


async def _paginate_comments(
    client: httpx.AsyncClient,
    file_id: str,
    headers: dict[str, str],
    capture_raw_pages: bool = False,
) -> tuple[
    list[dict],
    int | None,
    str | None,
    dict[str, str | int | None] | None,
    list[dict],
]:
    items: list[dict] = []
    raw_pages: list[dict] = []
    page_token: str | None = None
    status_code: int | None = None
    error_message: str | None = None
    error_details: dict[str, str | int | None] | None = None
    while True:
        params: dict = {
            "fields": (
                "comments(id,createdTime,author(emailAddress,displayName,me),"
                "replies(id,createdTime,author(emailAddress,displayName,me))),"
                "nextPageToken"
            ),
            "pageSize": 100,
            "includeDeleted": False,
        }
        if page_token:
            params["pageToken"] = page_token
        resp = await client.get(
            f"{_DRIVE_API}/files/{file_id}/comments",
            headers=headers,
            params=params,
        )
        status_code = resp.status_code
        if resp.status_code != 200:
            error_details = _parse_google_api_error(resp)
            error_message = str(error_details.get("message"))
            if capture_raw_pages:
                try:
                    raw_pages.append(resp.json())
                except ValueError:
                    raw_pages.append({"raw_text": resp.text[:2000]})
            break
        data = resp.json()
        if capture_raw_pages:
            raw_pages.append(data)
        items.extend(data.get("comments") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items, status_code, error_message, error_details, raw_pages
