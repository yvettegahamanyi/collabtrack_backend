import logging

import httpx

from app.services.lti.config import MOODLE_BASE_URL, MOODLE_WS_TOKEN

logger = logging.getLogger(__name__)


class MoodleClientError(Exception):
    pass


def _flatten_moodle_params(
    params: dict[str, object],
    prefix: str = "",
) -> dict[str, str]:
    """Encode nested Moodle REST parameters (e.g. groupids[0]=3)."""
    flat: dict[str, str] = {}
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    flat.update(_flatten_moodle_params(item, f"{full_key}[{index}]"))
                else:
                    flat[f"{full_key}[{index}]"] = str(item)
        elif isinstance(value, dict):
            flat.update(_flatten_moodle_params(value, full_key))
        elif value is not None:
            flat[full_key] = str(value)
    return flat


def moodle_ws_configured() -> bool:
    return bool(MOODLE_BASE_URL and MOODLE_WS_TOKEN)


async def moodle_ws_call(wsfunction: str, **params: object) -> object:
    if not moodle_ws_configured():
        raise MoodleClientError(
            "Moodle web services are not configured. Set MOODLE_BASE_URL and MOODLE_WS_TOKEN."
        )

    payload = _flatten_moodle_params(
        {
            "wstoken": MOODLE_WS_TOKEN,
            "wsfunction": wsfunction,
            "moodlewsrestformat": "json",
            **params,
        }
    )
    url = f"{MOODLE_BASE_URL}/webservice/rest/server.php"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, data=payload)

    if response.status_code != 200:
        raise MoodleClientError(
            f"Moodle API HTTP {response.status_code}: {response.text[:200]}"
        )

    data = response.json()
    if isinstance(data, dict) and data.get("exception"):
        message = data.get("message", "Unknown Moodle error")
        raise MoodleClientError(message)
    return data


async def get_course_groups(course_id: int) -> list[dict]:
    data = await moodle_ws_call(
        "core_group_get_course_groups",
        courseid=course_id,
    )
    if not isinstance(data, list):
        raise MoodleClientError("Unexpected response from core_group_get_course_groups.")
    return data


async def get_group_enrolled_users(course_id: int, group_id: int) -> list[dict]:
    """Return Moodle users in a group with email addresses."""
    del course_id  # reserved for future course-scoped enrolment checks

    members_data = await moodle_ws_call(
        "core_group_get_group_members",
        groupids=[group_id],
    )
    if not isinstance(members_data, list) or not members_data:
        return []

    user_ids: list[int] = []
    for entry in members_data:
        for user_id in entry.get("userids", []):
            user_ids.append(int(user_id))

    if not user_ids:
        return []

    return await get_users_by_field("id", user_ids)


async def get_users_by_field(field: str, values: list[int | str]) -> list[dict]:
    data = await moodle_ws_call(
        "core_user_get_users_by_field",
        field=field,
        values=values,
    )
    if not isinstance(data, list):
        return []
    return data


async def get_moodle_user_by_id(user_id: str | int) -> dict | None:
    users = await get_users_by_field("id", [int(user_id)])
    return users[0] if users else None
