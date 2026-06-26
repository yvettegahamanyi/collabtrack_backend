import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "CollabTrack")
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

# Import models so they are registered on Base.metadata.
from app import models  # noqa: F401
from app.database import engine
from app.routers import admin, assignments, auth, classes, dataset, groups, integrations, invites, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify the database connection on startup.
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    yield
    await engine.dispose()


API_DESCRIPTION = """
**CollabTrack** measures and reports individual student contributions across
shared group assets (GitHub repositories, Google Docs) and meeting transcripts.

### Response format
All endpoints return a standard envelope:

```json
{ "data": { ... }, "message": "Human-readable summary", "code": 200 }
```

Errors use the same shape with `data: null`.

### Authentication
Most endpoints require a **Bearer JWT**.

1. Create an account with `POST /auth/register` (returns a token immediately), or
   log in with `POST /auth/login`.
2. Copy the `access_token` from the response.
3. Click **Authorize** (top right) and paste the token — Swagger sends it as
   `Authorization: Bearer <token>` on every protected request.

### Roles
- `STUDENT` / `INSTRUCTOR` — chosen by the user during onboarding (`PATCH /users/me`).
- `ADMIN` — seeded via `python -m scripts.seed_admin`; can activate/deactivate users.
"""

tags_metadata = [
    {
        "name": "auth",
        "description": "Registration, login, and password reset. **Public** endpoints.",
    },
    {
        "name": "users",
        "description": "Profile management for the authenticated user "
        "(whoami + onboarding).",
    },
    {
        "name": "admin",
        "description": "User administration. Requires an **ADMIN** account.",
    },
    {
        "name": "groups",
        "description": "Project group CRUD, invitations, and member management.",
    },
    {
        "name": "invites",
        "description": "Public invite validation and authenticated invite acceptance.",
    },
    {
        "name": "dataset",
        "description": "Collab track benchmark dataset import and retrieval.",
    },
    {
        "name": "integrations",
        "description": "GitHub and Google OAuth connections for participation tracking.",
    },
    {
        "name": "classes",
        "description": "Instructor class management.",
    },
    {
        "name": "assignments",
        "description": "Assignment and report management within classes.",
    },
    # {
    #     "name": "health",
    #     "description": "Service and database liveness checks.",
    # },
]

app = FastAPI(
    title=APP_NAME,
    # description=API_DESCRIPTION,
    version="0.1.0",
    debug=DEBUG,
    lifespan=lifespan,
    openapi_tags=tags_metadata,
    contact={"name": "CollabTrack Team"},
    swagger_ui_parameters={
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "docExpansion": "none",
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _format_error_message(detail: object) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(part) for part in item.get("loc", ()))
                msg = item.get("msg", "Invalid value")
                parts.append(f"{loc}: {msg}" if loc else msg)
            else:
                parts.append(str(item))
        return "; ".join(parts) if parts else "Request failed."
    if isinstance(detail, dict):
        return str(detail.get("message", detail))
    return str(detail)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "data": None,
            "message": _format_error_message(exc.detail),
            "code": exc.status_code,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "data": None,
            "message": _format_error_message(exc.errors()),
            "code": 422,
        },
    )


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(groups.router)
app.include_router(invites.router)
app.include_router(dataset.router)
app.include_router(integrations.router)
app.include_router(classes.router)
app.include_router(assignments.router)


# @app.get("/", tags=["health"], summary="Service root")
# async def root():
#     """Basic service banner confirming the API is reachable."""
#     return {"app": settings.app_name, "status": "ok"}


# @app.get("/health", tags=["health"], summary="Liveness check")
# async def health():
#     """Lightweight check that does not touch the database."""
#     return {"status": "healthy"}


# @app.get("/health/db", tags=["health"], summary="Database connectivity check")
# async def health_db(db: AsyncSession = Depends(get_db)):
#     """Runs `SELECT 1` to confirm the database connection is healthy."""
#     result = await db.execute(text("SELECT 1"))
#     return {"database": "connected", "result": result.scalar()}
