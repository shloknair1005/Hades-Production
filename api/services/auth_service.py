import hashlib
import secrets
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from api.models.orm import User, Organization, RefreshToken
from api.models.schemas import RegisterRequest, LoginRequest
from api.core.jwt import create_access_token, create_refresh_token, decode_token
import bcrypt


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def register(req: RegisterRequest, db: AsyncSession) -> dict:
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    slug_taken = await db.execute(select(Organization).where(Organization.slug == req.org_slug))
    if slug_taken.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Org slug already taken")

    org = Organization(name=req.org_name, slug=req.org_slug)
    db.add(org)
    await db.flush()

    user = User(
        email=req.email,
        display_name=req.display_name,
        password_hash=_hash_password(req.password),
        org_id=org.id,
        role="admin",
        is_power_user=True,
    )
    db.add(user)
    await db.flush()

    return await _issue_tokens(user, db)


async def login(req: LoginRequest, db: AsyncSession) -> dict:
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user or not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.last_login_at = datetime.now(timezone.utc)
    return await _issue_tokens(user, db)


async def refresh(token: str, db: AsyncSession) -> dict:
    payload = decode_token(token, expected_type="refresh")
    token_hash = _hash_token(token)

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
        )
    )
    stored = result.scalar_one_or_none()
    if not stored or stored.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token invalid or expired")

    stored.revoked_at = datetime.now(timezone.utc)

    user_result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return await _issue_tokens(user, db)


async def logout(token: str, db: AsyncSession) -> None:
    token_hash = _hash_token(token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored:
        stored.revoked_at = datetime.now(timezone.utc)


async def _issue_tokens(user: User, db: AsyncSession) -> dict:
    access = create_access_token(
        user_id=user.id,
        role=user.role,
        org_id=user.org_id or "",
        is_power_user=user.is_power_user,
    )
    refresh_token_str, expires_at = create_refresh_token(user.id)

    rt = RefreshToken(
        user_id=user.id,
        token_hash=_hash_token(refresh_token_str),
        expires_at=expires_at,
    )
    db.add(rt)

    return {"access_token": access, "refresh_token": refresh_token_str, "token_type": "bearer"}
