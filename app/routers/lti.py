import asyncio
import logging
import os
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pylti1p3.exception import LtiException, OIDCException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.lti.config import get_tool_config, require_lti_configured
from app.services.lti.fastapi_adapter import (
    FastAPIMessageLaunch,
    FastAPIOIDCLogin,
    build_fastapi_request,
)
from app.services.moodle_sync import handle_instructor_lti_launch

load_dotenv()

FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "https://collabtrackfrontend-production.up.railway.app",
)
LTI_LAUNCH_URL = os.getenv("LTI_LAUNCH_URL", "")

router = APIRouter(prefix="/lti", tags=["lti"])
logger = logging.getLogger(__name__)


def _form_dict(form) -> dict[str, str]:
    data: dict[str, str] = {}
    for key, value in form.multi_items():
        if isinstance(value, str):
            data[key] = value
    return data


@router.get("/jwks", summary="LTI tool JWKS")
def lti_jwks():
    """Public keys Moodle uses to verify CollabTrack grade passback tokens."""
    require_lti_configured()
    tool_conf = get_tool_config()
    return JSONResponse(content=tool_conf.get_jwks())


@router.post("/login", summary="LTI OIDC login initiation", include_in_schema=True)
async def lti_login(request: Request):
    """Moodle redirects here first; we forward the instructor to Moodle OIDC auth."""
    require_lti_configured()
    form = await request.form()
    form_data = _form_dict(form)
    target_link_uri = form_data.get("target_link_uri") or LTI_LAUNCH_URL
    if not target_link_uri and not LTI_LAUNCH_URL:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="target_link_uri is required (or set LTI_LAUNCH_URL).",
        )

    # Moodle requires redirect_uri to exactly match a registered redirection URI.
    # Always prefer the configured launch URL so it matches Moodle tool settings.
    launch_url = LTI_LAUNCH_URL or target_link_uri
    if (
        LTI_LAUNCH_URL
        and target_link_uri
        and target_link_uri.rstrip("/") != LTI_LAUNCH_URL.rstrip("/")
    ):
        logger.warning(
            "Moodle target_link_uri differs from LTI_LAUNCH_URL; using configured value. "
            "target_link_uri=%s lti_launch_url=%s",
            target_link_uri,
            LTI_LAUNCH_URL,
        )

    def _redirect():
        tool_conf = get_tool_config()
        fastapi_request = build_fastapi_request(request, form_data)
        oidc = FastAPIOIDCLogin(fastapi_request, tool_conf)
        return oidc.redirect(launch_url)

    try:
        return await asyncio.to_thread(_redirect)
    except OIDCException as exc:
        logger.warning("LTI OIDC login failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/launch", summary="LTI resource launch", include_in_schema=True)
async def lti_launch(request: Request, db: AsyncSession = Depends(get_db)):
    """Validate the Moodle launch, provision class/assignment/groups, sign in instructor."""
    require_lti_configured()
    form = await request.form()
    form_data = _form_dict(form)

    def _validate_launch() -> dict:
        tool_conf = get_tool_config()
        fastapi_request = build_fastapi_request(request, form_data)
        message_launch = FastAPIMessageLaunch(fastapi_request, tool_conf)
        return message_launch.get_launch_data()

    try:
        launch_data = await asyncio.to_thread(_validate_launch)
    except LtiException as exc:
        logger.warning("LTI launch validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    result = await handle_instructor_lti_launch(db, launch_data)

    params = {
        "token": result.access_token,
        "assignment_id": result.assignment_id,
        "class_id": result.class_id,
        "groups_imported": str(result.groups_imported),
        "members_added": str(result.members_added),
    }
    if result.warnings:
        params["warning"] = result.warnings[0][:200]

    redirect_url = f"{FRONTEND_URL.rstrip('/')}/auth/lti-callback?{urlencode(params)}"
    return RedirectResponse(url=redirect_url, status_code=302)
