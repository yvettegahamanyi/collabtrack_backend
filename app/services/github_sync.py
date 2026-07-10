from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

import httpx

from app.schemas.participation import GithubMetrics

_GITHUB_API = "https://api.github.com"


@dataclass
class GithubSyncEventRecord:
    type: str
    owner: str
    repo: str
    source_id: str | None
    author_login: str | None
    author_email: str | None
    matched_login: str | None
    matched_email: str | None
    match_method: str | None
    timestamp: str | None = None
    message: str | None = None
    additions: int | None = None
    deletions: int | None = None
    lines_changed: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GithubSyncResult:
    by_login: dict[str, GithubMetrics] = field(default_factory=dict)
    by_email: dict[str, GithubMetrics] = field(default_factory=dict)
    events_by_login: dict[str, list[GithubSyncEventRecord]] = field(
        default_factory=dict
    )
    events_by_email: dict[str, list[GithubSyncEventRecord]] = field(
        default_factory=dict
    )
    login_public_emails: dict[str, str | None] = field(default_factory=dict)
    login_git_emails: dict[str, set[str]] = field(default_factory=dict)
    commits_fetched: int = 0

    def get_or_create_login(self, login: str) -> GithubMetrics:
        if login not in self.by_login:
            self.by_login[login] = GithubMetrics()
        return self.by_login[login]

    def get_or_create_email(self, email: str) -> GithubMetrics:
        key = email.lower()
        if key not in self.by_email:
            self.by_email[key] = GithubMetrics()
        return self.by_email[key]

    def get_or_create(self, login: str) -> GithubMetrics:
        return self.get_or_create_login(login)

    def record_event_for_login(
        self, login: str, event: GithubSyncEventRecord
    ) -> None:
        self.events_by_login.setdefault(login, []).append(event)

    def record_event_for_email(
        self, email: str, event: GithubSyncEventRecord
    ) -> None:
        self.events_by_email.setdefault(email.lower(), []).append(event)


def _list_params(*, since: datetime | None, extra: dict | None = None) -> dict:
    params = {"per_page": 100, **(extra or {})}
    if since is not None:
        params["since"] = since.isoformat().replace("+00:00", "Z")
    return params


async def _add_commit_stats(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    owner: str,
    repo: str,
    commit: dict,
    metrics: GithubMetrics,
    result: GithubSyncResult | None = None,
    matched_login: str | None = None,
    matched_email: str | None = None,
    match_method: str | None = None,
) -> None:
    metrics.total_commits += 1
    sha = commit.get("sha")
    commit_payload = commit.get("commit") or {}
    author_payload = commit_payload.get("author") or {}
    author_login = (commit.get("author") or {}).get("login")
    author_email = (author_payload.get("email") or "").lower() or None
    timestamp = author_payload.get("date")
    message = commit_payload.get("message")
    additions = 0
    deletions = 0
    if sha:
        detail = await client.get(
            f"{_GITHUB_API}/repos/{owner}/{repo}/commits/{sha}",
            headers=headers,
        )
        if detail.status_code == 200:
            stats = detail.json().get("stats") or {}
            additions = int(stats.get("additions", 0))
            deletions = int(stats.get("deletions", 0))
            metrics.lines_changed += additions + deletions
    if result is not None:
        event = GithubSyncEventRecord(
            type="commit",
            owner=owner,
            repo=repo,
            source_id=sha,
            author_login=author_login,
            author_email=author_email,
            matched_login=matched_login,
            matched_email=matched_email.lower() if matched_email else None,
            match_method=match_method,
            timestamp=timestamp,
            message=message,
            additions=additions,
            deletions=deletions,
            lines_changed=additions + deletions,
        )
        if matched_login:
            result.record_event_for_login(matched_login, event)
        if matched_email:
            result.record_event_for_email(matched_email, event)


def _record_github_event(
    *,
    result: GithubSyncResult,
    event_type: str,
    owner: str,
    repo: str,
    source_id: str | None,
    author_login: str | None,
    author_email: str | None,
    matched_login: str | None,
    matched_email: str | None,
    match_method: str | None,
    timestamp: str | None = None,
) -> None:
    event = GithubSyncEventRecord(
        type=event_type,
        owner=owner,
        repo=repo,
        source_id=source_id,
        author_login=author_login,
        author_email=author_email,
        matched_login=matched_login,
        matched_email=matched_email.lower() if matched_email else None,
        match_method=match_method,
        timestamp=timestamp,
    )
    if matched_login:
        result.record_event_for_login(matched_login, event)
    if matched_email:
        result.record_event_for_email(matched_email, event)


async def sync_github_repo(
    *,
    access_token: str,
    owner: str,
    repo: str,
    logins: set[str],
    since: datetime | None = None,
) -> GithubSyncResult:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    result = GithubSyncResult()

    async with httpx.AsyncClient(timeout=30.0) as client:
        for login in logins:
            metrics = result.get_or_create(login)
            commits = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/commits",
                headers,
                params=_list_params(since=since, extra={"author": login}),
            )
            for commit in commits:
                await _add_commit_stats(
                    client,
                    headers=headers,
                    owner=owner,
                    repo=repo,
                    commit=commit,
                    metrics=metrics,
                    result=result,
                    matched_login=login,
                    match_method="login_filter",
                )

        pulls = await _paginate(
            client,
            f"{_GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers,
            params={"state": "all", "per_page": 100},
        )
        for pr in pulls:
            user_login = (pr.get("user") or {}).get("login")
            if user_login in logins:
                result.get_or_create(user_login).prs_created += 1
                _record_github_event(
                    result=result,
                    event_type="pr_created",
                    owner=owner,
                    repo=repo,
                    source_id=str(pr.get("number")),
                    author_login=user_login,
                    author_email=None,
                    matched_login=user_login,
                    matched_email=None,
                    match_method="login_filter",
                    timestamp=pr.get("created_at"),
                )

        for pr in pulls:
            pr_number = pr.get("number")
            if not pr_number:
                continue
            reviews = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers,
                params={"per_page": 100},
            )
            for review in reviews:
                reviewer = (review.get("user") or {}).get("login")
                if reviewer in logins:
                    result.get_or_create(reviewer).prs_reviewed += 1
                    _record_github_event(
                        result=result,
                        event_type="pr_reviewed",
                        owner=owner,
                        repo=repo,
                        source_id=str(review.get("id") or f"{pr_number}:{reviewer}"),
                        author_login=reviewer,
                        author_email=None,
                        matched_login=reviewer,
                        matched_email=None,
                        match_method="login_filter",
                        timestamp=review.get("submitted_at"),
                    )

            issue_comments = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers,
                params={"per_page": 100},
            )
            for comment in issue_comments:
                author = (comment.get("user") or {}).get("login")
                if author in logins:
                    result.get_or_create(author).comments += 1
                    _record_github_event(
                        result=result,
                        event_type="comment",
                        owner=owner,
                        repo=repo,
                        source_id=str(comment.get("id")),
                        author_login=author,
                        author_email=None,
                        matched_login=author,
                        matched_email=None,
                        match_method="login_filter",
                        timestamp=comment.get("created_at"),
                    )

            review_comments = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                headers,
                params={"per_page": 100},
            )
            for comment in review_comments:
                author = (comment.get("user") or {}).get("login")
                if author in logins:
                    result.get_or_create(author).comments += 1
                    _record_github_event(
                        result=result,
                        event_type="comment",
                        owner=owner,
                        repo=repo,
                        source_id=str(comment.get("id")),
                        author_login=author,
                        author_email=None,
                        matched_login=author,
                        matched_email=None,
                        match_method="login_filter",
                        timestamp=comment.get("created_at"),
                    )

        issues = await _paginate(
            client,
            f"{_GITHUB_API}/repos/{owner}/{repo}/issues",
            headers,
            params={"state": "all", "per_page": 100},
        )
        for issue in issues:
            if issue.get("pull_request"):
                continue
            issue_number = issue.get("number")
            if not issue_number:
                continue
            comments = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                headers,
                params={"per_page": 100},
            )
            for comment in comments:
                author = (comment.get("user") or {}).get("login")
                if author in logins:
                    result.get_or_create(author).comments += 1
                    _record_github_event(
                        result=result,
                        event_type="comment",
                        owner=owner,
                        repo=repo,
                        source_id=str(comment.get("id")),
                        author_login=author,
                        author_email=None,
                        matched_login=author,
                        matched_email=None,
                        match_method="login_filter",
                        timestamp=comment.get("created_at"),
                    )

    return result


async def sync_github_repo_by_email(
    *,
    access_token: str,
    owner: str,
    repo: str,
    emails: set[str],
    since: datetime | None = None,
) -> GithubSyncResult:
    """Attribute GitHub activity by commit author login and git author email."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    result = GithubSyncResult()
    normalized = {email.lower() for email in emails}
    login_email_cache: dict[str, str | None] = {}

    async def resolve_login_email(client: httpx.AsyncClient, login: str) -> str | None:
        if login in login_email_cache:
            return login_email_cache[login]
        resp = await client.get(f"{_GITHUB_API}/users/{login}", headers=headers)
        email = None
        if resp.status_code == 200:
            email = (resp.json().get("email") or "").lower() or None
        login_email_cache[login] = email
        result.login_public_emails[login] = email
        return email

    async def attribute_login(
        client: httpx.AsyncClient,
        login: str | None,
        increment: str,
        *,
        owner: str,
        repo: str,
        source_id: str | None,
        timestamp: str | None = None,
    ) -> None:
        if not login:
            return
        login_metrics = result.get_or_create_login(login)
        setattr(login_metrics, increment, getattr(login_metrics, increment) + 1)
        email = await resolve_login_email(client, login)
        matched_email = email if email and email in normalized else None
        if matched_email:
            email_metrics = result.get_or_create_email(matched_email)
            setattr(email_metrics, increment, getattr(email_metrics, increment) + 1)
        event_type = {
            "prs_created": "pr_created",
            "prs_reviewed": "pr_reviewed",
            "comments": "comment",
        }[increment]
        _record_github_event(
            result=result,
            event_type=event_type,
            owner=owner,
            repo=repo,
            source_id=source_id,
            author_login=login,
            author_email=email,
            matched_login=login,
            matched_email=matched_email,
            match_method="public_email" if matched_email else "author_login",
            timestamp=timestamp,
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        commits = await _paginate(
            client,
            f"{_GITHUB_API}/repos/{owner}/{repo}/commits",
            headers,
            params=_list_params(since=since),
        )
        result.commits_fetched += len(commits)
        for commit in commits:
            author_login = (commit.get("author") or {}).get("login")
            git_email = (
                ((commit.get("commit") or {}).get("author") or {}).get("email") or ""
            ).lower()

            if author_login:
                await resolve_login_email(client, author_login)
                if git_email:
                    result.login_git_emails.setdefault(author_login, set()).add(
                        git_email
                    )
                await _add_commit_stats(
                    client,
                    headers=headers,
                    owner=owner,
                    repo=repo,
                    commit=commit,
                    metrics=result.get_or_create_login(author_login),
                    result=result,
                    matched_login=author_login,
                    match_method="author_login",
                )

            if git_email in normalized:
                await _add_commit_stats(
                    client,
                    headers=headers,
                    owner=owner,
                    repo=repo,
                    commit=commit,
                    metrics=result.get_or_create_email(git_email),
                    result=result,
                    matched_email=git_email,
                    match_method="git_author_email",
                )

            if author_login:
                public_email = login_email_cache.get(author_login)
                if (
                    public_email
                    and public_email in normalized
                    and public_email != git_email
                ):
                    await _add_commit_stats(
                        client,
                        headers=headers,
                        owner=owner,
                        repo=repo,
                        commit=commit,
                        metrics=result.get_or_create_email(public_email),
                        result=result,
                        matched_email=public_email,
                        match_method="public_email",
                    )

        pulls = await _paginate(
            client,
            f"{_GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers,
            params={"state": "all", "per_page": 100},
        )
        for pr in pulls:
            await attribute_login(
                client,
                (pr.get("user") or {}).get("login"),
                "prs_created",
                owner=owner,
                repo=repo,
                source_id=str(pr.get("number")),
                timestamp=pr.get("created_at"),
            )

        for pr in pulls:
            pr_number = pr.get("number")
            if not pr_number:
                continue
            reviews = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers,
                params={"per_page": 100},
            )
            for review in reviews:
                await attribute_login(
                    client,
                    (review.get("user") or {}).get("login"),
                    "prs_reviewed",
                    owner=owner,
                    repo=repo,
                    source_id=str(review.get("id") or f"{pr_number}:{review.get('user', {}).get('login')}"),
                    timestamp=review.get("submitted_at"),
                )

            issue_comments = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers,
                params={"per_page": 100},
            )
            for comment in issue_comments:
                await attribute_login(
                    client,
                    (comment.get("user") or {}).get("login"),
                    "comments",
                    owner=owner,
                    repo=repo,
                    source_id=str(comment.get("id")),
                    timestamp=comment.get("created_at"),
                )

            review_comments = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                headers,
                params={"per_page": 100},
            )
            for comment in review_comments:
                await attribute_login(
                    client,
                    (comment.get("user") or {}).get("login"),
                    "comments",
                    owner=owner,
                    repo=repo,
                    source_id=str(comment.get("id")),
                    timestamp=comment.get("created_at"),
                )

        issues = await _paginate(
            client,
            f"{_GITHUB_API}/repos/{owner}/{repo}/issues",
            headers,
            params={"state": "all", "per_page": 100},
        )
        for issue in issues:
            if issue.get("pull_request"):
                continue
            issue_number = issue.get("number")
            if not issue_number:
                continue
            comments = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                headers,
                params={"per_page": 100},
            )
            for comment in comments:
                await attribute_login(
                    client,
                    (comment.get("user") or {}).get("login"),
                    "comments",
                    owner=owner,
                    repo=repo,
                    source_id=str(comment.get("id")),
                    timestamp=comment.get("created_at"),
                )

    return result


def merge_github_results(
    target: GithubSyncResult, source: GithubSyncResult
) -> GithubSyncResult:
    for login, metrics in source.by_login.items():
        existing = target.get_or_create_login(login)
        existing.total_commits += metrics.total_commits
        existing.lines_changed += metrics.lines_changed
        existing.prs_created += metrics.prs_created
        existing.prs_reviewed += metrics.prs_reviewed
        existing.comments += metrics.comments
    for email, metrics in source.by_email.items():
        existing = target.get_or_create_email(email)
        existing.total_commits += metrics.total_commits
        existing.lines_changed += metrics.lines_changed
        existing.prs_created += metrics.prs_created
        existing.prs_reviewed += metrics.prs_reviewed
        existing.comments += metrics.comments
    target.commits_fetched += source.commits_fetched
    target.login_public_emails.update(source.login_public_emails)
    for login, emails in source.login_git_emails.items():
        target.login_git_emails.setdefault(login, set()).update(emails)
    for login, events in source.events_by_login.items():
        target.events_by_login.setdefault(login, []).extend(events)
    for email, events in source.events_by_email.items():
        target.events_by_email.setdefault(email, []).extend(events)
    return target


async def _paginate(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict | None = None,
) -> list[dict]:
    items: list[dict] = []
    page = 1
    base_params = dict(params or {})
    while True:
        base_params["page"] = page
        resp = await client.get(url, headers=headers, params=base_params)
        if resp.status_code != 200:
            break
        batch = resp.json()
        if not batch:
            break
        if isinstance(batch, list):
            items.extend(batch)
            if len(batch) < base_params.get("per_page", 100):
                break
            page += 1
        else:
            break
    return items
