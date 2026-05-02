from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import jwt
from fastapi import HTTPException, status
from api.core.config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, role: str, org_id: str, is_power_user: bool) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "org_id": org_id,
        "is_power_user": is_power_user,
        "exp": _now() + timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES),
        "iat": _now(),
        "jti": str(uuid.uuid4()),
        "type": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> tuple[str, datetime]:
    expires_at = _now() + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "exp": expires_at,
        "iat": _now(),
        "jti": str(uuid.uuid4()),
        "type": "refresh",
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, expires_at


def decode_token(token: str, expected_type: str = "access") -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != expected_type:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
