"""Locust load tests for CollabTrack API endpoints.

Usage (backend must be running):

    locust -f locustfile.py --headless -u 50 -r 5 -t 2m --html performance/report.html

Environment variables (defaults match scripts/seed_e2e_users.py):

    E2E_INSTRUCTOR_EMAIL, E2E_INSTRUCTOR_PASSWORD
    E2E_STUDENT_EMAIL, E2E_STUDENT_PASSWORD
    LOCUST_HOST (default http://127.0.0.1:8000)
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task


def _login(client, email: str, password: str) -> str | None:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        name="/auth/login",
    )
    if response.status_code != 200:
        return None
    return response.json()["data"]["access_token"]


class InstructorUser(HttpUser):
    wait_time = between(1, 3)
    weight = 2

    def on_start(self) -> None:
        email = os.getenv("E2E_INSTRUCTOR_EMAIL", "e2e.instructor@example.com")
        password = os.getenv("E2E_INSTRUCTOR_PASSWORD", "E2ETest123!")
        self.token = _login(self.client, email, password)

    def _auth_headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    @task(3)
    def list_classes(self) -> None:
        self.client.get("/classes", headers=self._auth_headers(), name="/classes")

    @task(2)
    def instructor_dashboard(self) -> None:
        self.client.get(
            "/instructor/dashboard",
            headers=self._auth_headers(),
            name="/instructor/dashboard",
        )

    @task(1)
    def profile(self) -> None:
        self.client.get("/users/me", headers=self._auth_headers(), name="/users/me")


class StudentUser(HttpUser):
    wait_time = between(1, 3)
    weight = 3

    def on_start(self) -> None:
        email = os.getenv("E2E_STUDENT_EMAIL", "e2e.student@example.com")
        password = os.getenv("E2E_STUDENT_PASSWORD", "E2ETest123!")
        self.token = _login(self.client, email, password)

    def _auth_headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    @task(4)
    def list_groups(self) -> None:
        self.client.get("/groups", headers=self._auth_headers(), name="/groups")

    @task(1)
    def profile(self) -> None:
        self.client.get("/users/me", headers=self._auth_headers(), name="/users/me")


class AuthUser(HttpUser):
    """Simulates login traffic without a cached token."""

    wait_time = between(2, 5)
    weight = 1

    @task
    def login_instructor(self) -> None:
        email = os.getenv("E2E_INSTRUCTOR_EMAIL", "e2e.instructor@example.com")
        password = os.getenv("E2E_INSTRUCTOR_PASSWORD", "E2ETest123!")
        _login(self.client, email, password)
