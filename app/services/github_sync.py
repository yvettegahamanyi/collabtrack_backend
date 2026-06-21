from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.schemas.participation import GithubMetrics

_GITHUB_API = "https://api.github.com"


@dataclass
class GithubSyncResult:
    by_login: dict[str, GithubMetrics] = field(default_factory=dict)
    by_email: dict[str, GithubMetrics] = field(default_factory=dict)

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


async def sync_github_repo(
    *,
    access_token: str,
    owner: str,
    repo: str,
    since: datetime,
    logins: set[str],
) -> GithubSyncResult:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    result = GithubSyncResult()
    since_iso = since.isoformat().replace("+00:00", "Z")

    async with httpx.AsyncClient(timeout=30.0) as client:
        for login in logins:
            metrics = result.get_or_create(login)
            commits = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/commits",
                headers,
                params={"author": login, "since": since_iso, "per_page": 100},
            )
            metrics.total_commits = len(commits)
            for commit in commits:
                sha = commit.get("sha")
                if not sha:
                    continue
                detail = await client.get(
                    f"{_GITHUB_API}/repos/{owner}/{repo}/commits/{sha}",
                    headers=headers,
                )
                if detail.status_code == 200:
                    stats = detail.json().get("stats") or {}
                    metrics.lines_changed += stats.get("additions", 0) + stats.get(
                        "deletions", 0
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

    return result


async def sync_github_repo_by_email(
    *,
    access_token: str,
    owner: str,
    repo: str,
    since: datetime,
    emails: set[str],
) -> GithubSyncResult:
    """Attribute GitHub activity to members by commit author email."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    result = GithubSyncResult()
    normalized = {email.lower() for email in emails}
    since_iso = since.isoformat().replace("+00:00", "Z")
    login_email_cache: dict[str, str | None] = {}

    async def resolve_login_email(client: httpx.AsyncClient, login: str) -> str | None:
        if login in login_email_cache:
            return login_email_cache[login]
        resp = await client.get(f"{_GITHUB_API}/users/{login}", headers=headers)
        email = None
        if resp.status_code == 200:
            email = (resp.json().get("email") or "").lower() or None
        login_email_cache[login] = email
        return email

    async def attribute_login(
        client: httpx.AsyncClient, login: str | None, increment: str
    ) -> None:
        if not login:
            return
        email = await resolve_login_email(client, login)
        if email and email in normalized:
            metrics = result.get_or_create_email(email)
            setattr(metrics, increment, getattr(metrics, increment) + 1)

    async with httpx.AsyncClient(timeout=30.0) as client:
        commits = await _paginate(
            client,
            f"{_GITHUB_API}/repos/{owner}/{repo}/commits",
            headers,
            params={"since": since_iso, "per_page": 100},
        )
        for commit in commits:
            commit_data = commit.get("commit") or {}
            author = commit_data.get("author") or {}
            email = (author.get("email") or "").lower()
            if email not in normalized:
                continue
            metrics = result.get_or_create_email(email)
            metrics.total_commits += 1
            sha = commit.get("sha")
            if not sha:
                continue
            detail = await client.get(
                f"{_GITHUB_API}/repos/{owner}/{repo}/commits/{sha}",
                headers=headers,
            )
            if detail.status_code == 200:
                stats = detail.json().get("stats") or {}
                metrics.lines_changed += stats.get("additions", 0) + stats.get(
                    "deletions", 0
                )

        pulls = await _paginate(
            client,
            f"{_GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers,
            params={"state": "all", "per_page": 100},
        )
        for pr in pulls:
            await attribute_login(client, (pr.get("user") or {}).get("login"), "prs_created")

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
                    client, (review.get("user") or {}).get("login"), "prs_reviewed"
                )

            issue_comments = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers,
                params={"per_page": 100},
            )
            for comment in issue_comments:
                await attribute_login(
                    client, (comment.get("user") or {}).get("login"), "comments"
                )

            review_comments = await _paginate(
                client,
                f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                headers,
                params={"per_page": 100},
            )
            for comment in review_comments:
                await attribute_login(
                    client, (comment.get("user") or {}).get("login"), "comments"
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
                    client, (comment.get("user") or {}).get("login"), "comments"
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
