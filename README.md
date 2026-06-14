# CollabTrack Backend

FastAPI backend for **CollabTrack** — a capstone project tool that measures and reports individual student contributions across shared group assets (GitHub repositories, Google Docs, and meeting transcripts).

The API powers user authentication, project group management, OAuth integrations, and raw participation metrics that the frontend displays in contribution reports.

## Features

- **Authentication** — JWT-based register, login, and password reset
- **User onboarding** — Students and instructors choose a role after signup
- **Project groups** — Create groups, invite members, manage roles (student / instructor)
- **Integrations** — Connect GitHub and Google accounts via OAuth
- **Participation tracking** — Link repos and Google Docs to a group, sync raw metrics, view per-member contributions
- **Admin panel** — List users and activate / deactivate accounts
- **Benchmark dataset** — Import and retrieve the CollabTrack training dataset

## Tech stack

| Layer         | Technology                                        |
| ------------- | ------------------------------------------------- |
| Framework     | [FastAPI](https://fastapi.tiangolo.com/)          |
| Server        | Uvicorn (dev) / Gunicorn + Uvicorn workers (prod) |
| Database      | PostgreSQL via SQLAlchemy 2.0 (async)             |
| Migrations    | Alembic                                           |
| Auth          | JWT (PyJWT) + bcrypt                              |
| OAuth         | GitHub & Google OAuth 2.0                         |
| Token storage | Fernet encryption at rest                         |

## Project structure

```
app/
├── main.py              # FastAPI app, CORS, error handlers
├── config.py            # Environment settings
├── database.py          # Async engine and session
├── dependencies.py      # Auth dependencies (JWT)
├── models.py            # SQLAlchemy ORM models
├── core/
│   ├── security.py      # Password hashing, JWT, invite tokens
│   └── encryption.py    # OAuth token encryption
├── routers/             # API route handlers
├── schemas/             # Pydantic request/response models
└── services/            # Business logic (groups, sync, OAuth, etc.)
alembic/                 # Database migrations
scripts/seed_admin.py    # Create or update the admin account
```

## Prerequisites

- Python 3.11+
- PostgreSQL 14+
- GitHub OAuth App (for integrations)
- Google Cloud OAuth credentials (for integrations)

## Local setup

### 1. Clone and install

```bash
git clone https://github.com/yvettegahamanyi/collabtrack_backend.git
cd collabtrack-backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

### 3. Run migrations

```bash
alembic upgrade head
```

### 4. Seed the admin account (optional)

```bash
python -m scripts.seed_admin
```

Defaults are read from `.env` (`ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME`).

### 5. Start the dev server

```bash
uvicorn app.main:app --reload --port 8000
```

- **Swagger UI:** http://localhost:8000/docs

## Environment variables

| Variable                      | Required         | Description                                                   |
| ----------------------------- | ---------------- | ------------------------------------------------------------- |
| `DATABASE_URL`                | Yes              | PostgreSQL connection string (`postgresql+asyncpg://...`)     |
| `SECRET_KEY`                  | Yes              | JWT signing key — use a strong random value in production     |
| `FRONTEND_URL`                | Yes              | Frontend base URL for OAuth redirects and invite links        |
| `TOKEN_ENCRYPTION_KEY`        | Yes              | Fernet key for encrypting OAuth tokens at rest                |
| `GITHUB_CLIENT_ID`            | For integrations | GitHub OAuth app client ID                                    |
| `GITHUB_CLIENT_SECRET`        | For integrations | GitHub OAuth app client secret                                |
| `GITHUB_CALLBACK_URL`         | For integrations | Default: `http://localhost:8000/integrations/github/callback` |
| `GOOGLE_CLIENT_ID`            | For integrations | Google OAuth client ID                                        |
| `GOOGLE_CLIENT_SECRET`        | For integrations | Google OAuth client secret                                    |
| `GOOGLE_CALLBACK_URL`         | For integrations | Default: `http://localhost:8000/integrations/google/callback` |
| `APP_NAME`                    | No               | Application name (default: `CollabTrack`)                     |
| `ENVIRONMENT`                 | No               | `development` or `production`                                 |
| `DEBUG`                       | No               | Enable SQL echo and debug mode                                |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No               | JWT lifetime (default: 1440)                                  |

See `.env.example` for the full list.

## API overview

All endpoints return a standard envelope:

```json
{
  "data": {},
  "message": "Human-readable summary",
  "code": 200
}
```

Errors use the same shape with `"data": null`.

### Authentication (`/auth`)

| Method | Path                           | Auth   | Description                    |
| ------ | ------------------------------ | ------ | ------------------------------ |
| POST   | `/auth/register`               | Public | Create account; returns JWT    |
| POST   | `/auth/login`                  | Public | Log in with email and password |
| POST   | `/auth/request-password-reset` | Public | Issue a password reset token   |
| POST   | `/auth/reset-password`         | Public | Reset password with token      |

### Users (`/users`)

| Method | Path        | Auth   | Description                              |
| ------ | ----------- | ------ | ---------------------------------------- |
| GET    | `/users/me` | Bearer | Get current user profile                 |
| PATCH  | `/users/me` | Bearer | Update profile and set role (onboarding) |

### Groups (`/groups`)

| Method | Path                                           | Auth   | Description                          |
| ------ | ---------------------------------------------- | ------ | ------------------------------------ |
| POST   | `/groups`                                      | Bearer | Create a group (students only)       |
| GET    | `/groups`                                      | Bearer | List groups the user belongs to      |
| GET    | `/groups/{id}`                                 | Bearer | Get group details with members       |
| PUT    | `/groups/{id}`                                 | Bearer | Update group (owner only)            |
| DELETE | `/groups/{id}`                                 | Bearer | Delete group (owner only)            |
| POST   | `/groups/{id}/invite`                          | Bearer | Generate invite link                 |
| GET    | `/groups/{id}/members`                         | Bearer | List members                         |
| DELETE | `/groups/{id}/members/{user_id}`               | Bearer | Remove a member                      |
| GET    | `/groups/{id}/repos`                           | Bearer | List linked GitHub repos             |
| POST   | `/groups/{id}/repos`                           | Bearer | Link a repo (owner only)             |
| DELETE | `/groups/{id}/repos/{repo_id}`                 | Bearer | Unlink a repo (owner only)           |
| GET    | `/groups/{id}/documents`                       | Bearer | List linked Google Docs              |
| POST   | `/groups/{id}/documents`                       | Bearer | Link a doc (owner only)              |
| DELETE | `/groups/{id}/documents/{doc_id}`              | Bearer | Unlink a doc (owner only)            |
| POST   | `/groups/{id}/sync`                            | Bearer | Sync participation data (owner only) |
| GET    | `/groups/{id}/contributions`                   | Bearer | Raw metrics for all members          |
| GET    | `/groups/{id}/members/{user_id}/participation` | Bearer | Metrics for one member               |

### Invites (`/invite`)

| Method | Path                     | Auth   | Description                  |
| ------ | ------------------------ | ------ | ---------------------------- |
| GET    | `/invite/{token}`        | Public | Validate an invite link      |
| POST   | `/invite/{token}/accept` | Bearer | Accept invite and join group |

### Integrations (`/integrations`)

| Method | Path                               | Auth   | Description                       |
| ------ | ---------------------------------- | ------ | --------------------------------- |
| GET    | `/integrations`                    | Bearer | GitHub / Google connection status |
| GET    | `/integrations/github/connect-url` | Bearer | GitHub OAuth authorize URL        |
| GET    | `/integrations/github/callback`    | Public | GitHub OAuth callback (redirect)  |
| DELETE | `/integrations/github`             | Bearer | Disconnect GitHub                 |
| GET    | `/integrations/google/connect-url` | Bearer | Google OAuth authorize URL        |
| GET    | `/integrations/google/callback`    | Public | Google OAuth callback (redirect)  |
| DELETE | `/integrations/google`             | Bearer | Disconnect Google                 |

### Admin (`/admin`)

All routes require an `ADMIN` role.

| Method | Path                           | Description       |
| ------ | ------------------------------ | ----------------- |
| GET    | `/admin/users`                 | List all users    |
| POST   | `/admin/users/{id}/activate`   | Activate a user   |
| POST   | `/admin/users/{id}/deactivate` | Deactivate a user |

### Dataset (`/collab-track-dataset`)

| Method | Path                           | Auth   | Description                 |
| ------ | ------------------------------ | ------ | --------------------------- |
| GET    | `/collab-track-dataset`        | Bearer | List benchmark dataset rows |
| POST   | `/collab-track-dataset/upload` | Bearer | Upload CSV dataset          |

## Authentication

Protected endpoints require a Bearer JWT:

```
Authorization: Bearer <access_token>
```

**Roles:**

| Role         | How assigned                   | Capabilities                              |
| ------------ | ------------------------------ | ----------------------------------------- |
| `STUDENT`    | Onboarding (`PATCH /users/me`) | Create groups, connect integrations       |
| `INSTRUCTOR` | Onboarding                     | Join groups as instructor, invite members |
| `ADMIN`      | `scripts/seed_admin`           | Manage user accounts                      |

In Swagger UI, click **Authorize** and paste the `access_token` from register or login.

## Integrations

Students connect GitHub and Google in **Settings → Apps & Integrations**. Group owners link a repo and Google Doc, trigger a sync, and view raw participation counts.

### Identity matching

| Source      | Identifier     | Rule                                                 |
| ----------- | -------------- | ---------------------------------------------------- |
| CollabTrack | `user.email`   | Source of truth at signup                            |
| GitHub      | `github_login` | All GitHub metrics use OAuth login, not commit email |
| Google      | OAuth `email`  | Must match signup email (`email_matched` flag)       |

## Database migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "describe your change"

# Roll back one revision
alembic downgrade -1
```
