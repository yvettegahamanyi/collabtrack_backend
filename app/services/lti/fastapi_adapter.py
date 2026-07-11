from starlette.requests import Request as StarletteRequest
from starlette.responses import HTMLResponse, RedirectResponse, Response

from pylti1p3.cookie import CookieService
from pylti1p3.message_launch import MessageLaunch
from pylti1p3.oidc_login import OIDCLogin
from pylti1p3.redirect import Redirect
from pylti1p3.request import Request
from pylti1p3.session import SessionService

from app.services.lti.cache import CollabTrackCacheDataStorage


class FastAPIRequest(Request):
    def __init__(
        self,
        *,
        cookies: dict[str, str],
        request_data: dict[str, str],
        request_is_secure: bool,
        session: dict[str, object],
    ) -> None:
        super().__init__()
        self._cookies = cookies
        self._request_data = request_data
        self._request_is_secure = request_is_secure
        self._session = session

    @property
    def session(self) -> dict[str, object]:
        return self._session

    def get_param(self, key: str) -> str | None:
        return self._request_data.get(key)

    def get_cookie(self, key: str) -> str | None:
        return self._cookies.get(key)

    def is_secure(self) -> bool:
        return self._request_is_secure


class FastAPICookieService(CookieService):
    def __init__(self, request: FastAPIRequest) -> None:
        self._request = request
        self._cookie_data_to_set: dict[str, dict[str, object]] = {}

    def get_cookie(self, name: str) -> str | None:
        return self._request.get_cookie(self._get_key(name))

    def set_cookie(self, name: str, value: str | int, exp: int | None = 3600) -> None:
        self._cookie_data_to_set[self._get_key(name)] = {
            "value": value,
            "exp": exp,
        }

    def _get_key(self, key: str) -> str:
        return self._cookie_prefix + "-" + key

    def update_response(self, response: Response) -> None:
        for key, cookie_data in self._cookie_data_to_set.items():
            kwargs: dict[str, object] = {
                "key": key,
                "value": cookie_data["value"],
                "max_age": cookie_data["exp"],
                "path": "/",
                "httponly": True,
            }
            if self._request.is_secure():
                kwargs["secure"] = True
                kwargs["samesite"] = "none"
            response.set_cookie(**kwargs)  # type: ignore[arg-type]


class FastAPISessionService(SessionService):
    pass


class FastAPIRedirect(Redirect):
    def __init__(self, location: str, cookie_service: FastAPICookieService | None) -> None:
        super().__init__()
        self._location = location
        self._cookie_service = cookie_service

    def do_redirect(self) -> RedirectResponse:
        return self._process_response(RedirectResponse(url=self._location, status_code=302))

    def do_js_redirect(self) -> HTMLResponse:
        html = (
            f'<script type="text/javascript">window.location="{self._location}";</script>'
        )
        return self._process_response(HTMLResponse(content=html))

    def set_redirect_url(self, location: str) -> None:
        self._location = location

    def get_redirect_url(self) -> str:
        return self._location

    def _process_response(self, response: Response) -> Response:
        if self._cookie_service:
            self._cookie_service.update_response(response)
        return response


class FastAPIOIDCLogin(OIDCLogin):
    def __init__(
        self,
        request: FastAPIRequest,
        tool_config,
        session_service: SessionService | None = None,
        cookie_service: FastAPICookieService | None = None,
        launch_data_storage: CollabTrackCacheDataStorage | None = None,
    ) -> None:
        cookie_service = cookie_service or FastAPICookieService(request)
        session_service = session_service or FastAPISessionService(request)
        launch_data_storage = launch_data_storage or CollabTrackCacheDataStorage()
        super().__init__(
            request,
            tool_config,
            session_service,
            cookie_service,
            launch_data_storage,
        )

    def get_redirect(self, url: str) -> FastAPIRedirect:
        return FastAPIRedirect(url, self._cookie_service)  # type: ignore[arg-type]

    def get_response(self, html: str) -> HTMLResponse:
        return HTMLResponse(content=html)


class FastAPIMessageLaunch(MessageLaunch):
    def __init__(
        self,
        request: FastAPIRequest,
        tool_config,
        session_service: SessionService | None = None,
        cookie_service: FastAPICookieService | None = None,
        launch_data_storage: CollabTrackCacheDataStorage | None = None,
    ) -> None:
        cookie_service = cookie_service or FastAPICookieService(request)
        session_service = session_service or FastAPISessionService(request)
        launch_data_storage = launch_data_storage or CollabTrackCacheDataStorage()
        super().__init__(
            request,
            tool_config,
            session_service,
            cookie_service,
            launch_data_storage,
        )

    def _get_request_param(self, key: str) -> str | None:
        return self._request.get_param(key)


def build_fastapi_request(
    starlette_request: StarletteRequest,
    form_data: dict[str, str],
) -> FastAPIRequest:
    forwarded_proto = starlette_request.headers.get("x-forwarded-proto", "")
    is_secure = starlette_request.url.scheme == "https" or forwarded_proto == "https"
    return FastAPIRequest(
        cookies=dict(starlette_request.cookies),
        request_data=form_data,
        request_is_secure=is_secure,
        session={},
    )
