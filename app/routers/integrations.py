from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import IntegrationProvider, User
from app.schemas.integration import ConnectUrlOut, IntegrationsStatusOut
from app.schemas.response import ApiResponse, success
from app.services.integrations import (
    build_github_connect_url,
    build_google_connect_url,
    disconnect_integration,
    get_integrations_status,
    handle_github_callback,
    handle_google_callback,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get(
    "",
    response_model=ApiResponse[IntegrationsStatusOut],
    summary="Get connected integration status",
)
async def list_integrations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await get_integrations_status(current_user, db)
    return success(
        data=data,
        message="Integration status retrieved successfully.",
    )


@router.get(
    "/github/connect-url",
    response_model=ApiResponse[ConnectUrlOut],
    summary="Get GitHub OAuth connect URL",
)
async def github_connect_url(current_user: User = Depends(get_current_user)):
    url = build_github_connect_url(current_user)
    return success(data=ConnectUrlOut(url=url), message="Connect URL generated.")


@router.get(
    "/github/callback",
    summary="GitHub OAuth callback",
    include_in_schema=False,
)
async def github_callback(
    code: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if not code or not state:
        return RedirectResponse(
            url=f"{settings.frontend_url.rstrip('/')}/student/settings?integration=github&status=error"
        )
    redirect_url = await handle_github_callback(code, state, db)
    return RedirectResponse(url=redirect_url)


@router.delete(
    "/github",
    response_model=ApiResponse[None],
    summary="Disconnect GitHub integration",
)
async def disconnect_github(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await disconnect_integration(current_user, IntegrationProvider.GITHUB, db)
    return success(message="GitHub integration disconnected.")


@router.get(
    "/google/connect-url",
    response_model=ApiResponse[ConnectUrlOut],
    summary="Get Google OAuth connect URL",
)
async def google_connect_url(current_user: User = Depends(get_current_user)):
    url = build_google_connect_url(current_user)
    return success(data=ConnectUrlOut(url=url), message="Connect URL generated.")


@router.get(
    "/google/callback",
    summary="Google OAuth callback",
    include_in_schema=False,
)
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if not code or not state:
        return RedirectResponse(
            url=f"{settings.frontend_url.rstrip('/')}/student/settings?integration=google&status=error"
        )
    redirect_url = await handle_google_callback(code, state, db)
    return RedirectResponse(url=redirect_url)


@router.delete(
    "/google",
    response_model=ApiResponse[None],
    summary="Disconnect Google integration",
)
async def disconnect_google(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await disconnect_integration(current_user, IntegrationProvider.GOOGLE, db)
    return success(message="Google integration disconnected.")
