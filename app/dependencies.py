import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.database import get_db
from app.models import RoleType, User

bearer_scheme = HTTPBearer()

_credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(credentials.credentials)
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise _credentials_exception
    except jwt.PyJWTError:
        raise _credentials_exception

    user = await db.get(User, user_id)
    if user is None:
        raise _credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )
    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != RoleType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return current_user


async def get_current_instructor(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != RoleType.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Instructor privileges required.",
        )
    return current_user
