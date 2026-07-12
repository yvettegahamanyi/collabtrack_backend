import logging
import re

from app.services.lti.config import LTI_CLAIM_CUSTOM, LTI_CLAIM_LIS
from app.services.moodle_client import MoodleClientError, get_moodle_user_by_id, moodle_ws_configured

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def _is_email(value: str) -> bool:
    return bool(_EMAIL_PATTERN.match(value.strip()))


def email_from_launch_data(launch_data: dict) -> str:
    """Extract an email from common LTI 1.3 / Moodle launch claim locations."""
    direct = (launch_data.get("email") or "").strip()
    if direct:
        return direct

    lis = launch_data.get(LTI_CLAIM_LIS) or {}
    if isinstance(lis, dict):
        for key in (
            "person_contact_email_primary",
            "person_contact_email",
            "email",
        ):
            value = (lis.get(key) or "").strip()
            if value:
                return value

    custom = launch_data.get(LTI_CLAIM_CUSTOM) or {}
    if isinstance(custom, dict):
        for key in ("email", "user_email", "mail", "person_email"):
            value = (custom.get(key) or "").strip()
            if value:
                return value

    return ""


def name_from_launch_data(launch_data: dict) -> str:
    name = (launch_data.get("name") or "").strip()
    if name:
        return name

    given = (launch_data.get("given_name") or "").strip()
    family = (launch_data.get("family_name") or "").strip()
    combined = f"{given} {family}".strip()
    if combined:
        return combined

    lis = launch_data.get(LTI_CLAIM_LIS) or {}
    if isinstance(lis, dict):
        full = (lis.get("person_name_full") or "").strip()
        if full:
            return full
        lis_given = (lis.get("person_name_given") or "").strip()
        lis_family = (lis.get("person_name_family") or "").strip()
        lis_combined = f"{lis_given} {lis_family}".strip()
        if lis_combined:
            return lis_combined

    return ""


async def resolve_launch_identity(launch_data: dict) -> tuple[str, str]:
    """Resolve instructor email and display name from LTI claims or Moodle WS."""
    lti_sub = str(launch_data.get("sub", "")).strip()
    email = email_from_launch_data(launch_data)
    name = name_from_launch_data(launch_data)

    if not email and moodle_ws_configured() and lti_sub.isdigit():
        try:
            moodle_user = await get_moodle_user_by_id(lti_sub)
        except MoodleClientError as exc:
            logger.warning("Failed to resolve Moodle user %s via WS: %s", lti_sub, exc)
            moodle_user = None
        if moodle_user:
            email = (moodle_user.get("email") or "").strip()
            if not name:
                name = (moodle_user.get("fullname") or "").strip()
                if not name:
                    first = (moodle_user.get("firstname") or "").strip()
                    last = (moodle_user.get("lastname") or "").strip()
                    name = f"{first} {last}".strip()

    if not email and lti_sub and _is_email(lti_sub):
        email = lti_sub

    if not name and email:
        name = email.split("@")[0]

    return email, name
