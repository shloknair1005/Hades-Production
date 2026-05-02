from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from api.core.database import get_db
from api.core.jwt import decode_token
from api.models.orm import User, UserRole

bearer = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials, expected_type="access")
    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def require_power_or_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.admin and not current_user.is_power_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Power user or admin access required")
    return current_user
