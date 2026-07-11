import os

from dotenv import load_dotenv
from fastapi import HTTPException, status
from pylti1p3.tool_config import ToolConfDict

load_dotenv()

MOODLE_PLATFORM_ISS = os.getenv("MOODLE_PLATFORM_ISS", "").rstrip("/")
MOODLE_CLIENT_ID = os.getenv("MOODLE_CLIENT_ID", "")
MOODLE_AUTH_LOGIN_URL = os.getenv("MOODLE_AUTH_LOGIN_URL", "")
MOODLE_AUTH_TOKEN_URL = os.getenv("MOODLE_AUTH_TOKEN_URL", "")
MOODLE_KEY_SET_URL = os.getenv("MOODLE_KEY_SET_URL", "")
MOODLE_DEPLOYMENT_ID = os.getenv("MOODLE_DEPLOYMENT_ID", "")
MOODLE_BASE_URL = os.getenv("MOODLE_BASE_URL", "").rstrip("/")
MOODLE_WS_TOKEN = os.getenv("MOODLE_WS_TOKEN", "")

LTI_TOOL_PRIVATE_KEY = os.getenv("LTI_TOOL_PRIVATE_KEY", "")
LTI_TOOL_PUBLIC_KEY = os.getenv("LTI_TOOL_PUBLIC_KEY", "")

LTI_CLAIM_CONTEXT = "https://purl.imsglobal.org/spec/lti/claim/context"
LTI_CLAIM_RESOURCE_LINK = "https://purl.imsglobal.org/spec/lti/claim/resource_link"
LTI_CLAIM_ROLES = "https://purl.imsglobal.org/spec/lti/claim/roles"

_INSTRUCTOR_ROLES = {
    "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Instructor",
    "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator",
    "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
    "http://purl.imsglobal.org/vocab/lis/v2/membership#ContentDeveloper",
    "http://purl.imsglobal.org/vocab/lis/v2/membership#TeachingAssistant",
    "http://purl.imsglobal.org/vocab/lis/v2/system/person#Administrator",
}


def lti_is_configured() -> bool:
    return bool(
        MOODLE_PLATFORM_ISS
        and MOODLE_CLIENT_ID
        and MOODLE_AUTH_LOGIN_URL
        and MOODLE_AUTH_TOKEN_URL
        and LTI_TOOL_PRIVATE_KEY
        and LTI_TOOL_PUBLIC_KEY
    )


def require_lti_configured() -> None:
    if not lti_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LTI is not configured. Set MOODLE_PLATFORM_ISS, MOODLE_CLIENT_ID, "
                "MOODLE_AUTH_LOGIN_URL, MOODLE_AUTH_TOKEN_URL, LTI_TOOL_PRIVATE_KEY, "
                "and LTI_TOOL_PUBLIC_KEY."
            ),
        )


def _normalize_pem(key: str, *, private: bool) -> str:
    """Accept full PEM blocks or single-line base64 bodies from hosting env vars."""
    normalized = key.replace("\\n", "\n").strip()
    if "BEGIN" in normalized:
        return normalized

    body = "".join(normalized.split())
    if not body:
        return normalized

    header = "-----BEGIN PRIVATE KEY-----" if private else "-----BEGIN PUBLIC KEY-----"
    footer = "-----END PRIVATE KEY-----" if private else "-----END PUBLIC KEY-----"
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"{header}\n{wrapped}\n{footer}\n"


def get_tool_config() -> ToolConfDict:
    require_lti_configured()
    deployment_ids = [MOODLE_DEPLOYMENT_ID] if MOODLE_DEPLOYMENT_ID else []
    issuer_config: dict[str, object] = {
        "default": True,
        "client_id": MOODLE_CLIENT_ID,
        "auth_login_url": MOODLE_AUTH_LOGIN_URL,
        "auth_token_url": MOODLE_AUTH_TOKEN_URL,
        "deployment_ids": deployment_ids,
    }
    if MOODLE_KEY_SET_URL:
        issuer_config["key_set_url"] = MOODLE_KEY_SET_URL

    settings = {MOODLE_PLATFORM_ISS: [issuer_config]}
    tool_conf = ToolConfDict(settings)
    tool_conf.set_private_key(
        MOODLE_PLATFORM_ISS,
        _normalize_pem(LTI_TOOL_PRIVATE_KEY, private=True),
        client_id=MOODLE_CLIENT_ID,
    )
    tool_conf.set_public_key(
        MOODLE_PLATFORM_ISS,
        _normalize_pem(LTI_TOOL_PUBLIC_KEY, private=False),
        client_id=MOODLE_CLIENT_ID,
    )
    return tool_conf


def is_instructor_launch(roles: list[str]) -> bool:
    return any(role in _INSTRUCTOR_ROLES for role in roles)
