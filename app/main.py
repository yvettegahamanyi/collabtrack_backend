from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Import models so they are registered on Base.metadata.
from app import models  # noqa: F401
from app.config import settings
from app.database import engine, get_db
from app.routers import admin, auth, groups, invites, users


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

### Authentication
Most endpoints require a **Bearer JWT**.

1. Create an account with `POST /auth/register` (returns a token immediately), or
   log in with `POST /auth/login`.
2. Click the **Authorize** button (top right) and paste your credentials —
   `/auth/login` is wired into Swagger, so authorizing here applies the token to
   every protected request.

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
    # {
    #     "name": "health",
    #     "description": "Service and database liveness checks.",
    # },
]

app = FastAPI(
    title=settings.app_name,
    # description=API_DESCRIPTION,
    version="0.1.0",
    debug=settings.debug,
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


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(groups.router)
app.include_router(invites.router)


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
