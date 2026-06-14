from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.schemas.participation import GoogleDocsMetrics

_DRIVE_API = "https://www.googleapis.com/drive/v3"


@dataclass
class GoogleSyncResult:
    by_email: dict[str, GoogleDocsMetrics] = field(default_factory=dict)

    def get_or_create(self, email: str) -> GoogleDocsMetrics:
        key = email.lower()
        if key not in self.by_email:
            self.by_email[key] = GoogleDocsMetrics()
        return self.by_email[key]


async def sync_google_doc(
    *,
    access_token: str,
    file_id: str,
    emails: set[str],
) -> GoogleSyncResult:
    headers = {"Authorization": f"Bearer {access_token}"}
    result = GoogleSyncResult()
    normalized = {email.lower() for email in emails}

    async with httpx.AsyncClient(timeout=30.0) as client:
        revisions = await _paginate(
            client,
            f"{_DRIVE_API}/files/{file_id}/revisions",
            headers,
            params={"pageSize": 100, "fields": "revisions(id,lastModifyingUser)"},
        )
        for revision in revisions:
            user = revision.get("lastModifyingUser") or {}
            email = (user.get("emailAddress") or "").lower()
            if email in normalized:
                result.get_or_create(email).edits += 1

        comments = await _paginate_comments(client, file_id, headers)
        for comment in comments:
            author = comment.get("author") or {}
            email = (author.get("emailAddress") or "").lower()
            if email in normalized:
                result.get_or_create(email).comments += 1
            for reply in comment.get("replies") or []:
                reply_author = reply.get("author") or {}
                reply_email = (reply_author.get("emailAddress") or "").lower()
                if reply_email in normalized:
                    result.get_or_create(reply_email).comments += 1

    return result


def merge_google_results(
    target: GoogleSyncResult, source: GoogleSyncResult
) -> GoogleSyncResult:
    for email, metrics in source.by_email.items():
        existing = target.get_or_create(email)
        existing.edits += metrics.edits
        existing.comments += metrics.comments
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
) -> list[dict]:
    items: list[dict] = []
    page_token: str | None = None
    while True:
        query = dict(params or {})
        if page_token:
            query["pageToken"] = page_token
        resp = await client.get(url, headers=headers, params=query)
        if resp.status_code != 200:
            break
        data = resp.json()
        items.extend(data.get("revisions") or data.get("files") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items


async def _paginate_comments(
    client: httpx.AsyncClient,
    file_id: str,
    headers: dict[str, str],
) -> list[dict]:
    items: list[dict] = []
    page_token: str | None = None
    while True:
        params: dict = {
            "fields": "comments(author,replies(author)),nextPageToken",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        resp = await client.get(
            f"{_DRIVE_API}/files/{file_id}/comments",
            headers=headers,
            params=params,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        items.extend(data.get("comments") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items
