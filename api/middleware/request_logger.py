import asyncio
import json
import time
import uuid
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_auth_fail_windows: dict[str, list[float]] = defaultdict(list)
_SLOW_THRESHOLD_MS = 10_000
_REPEATED_401_THRESHOLD = 5


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _extract_user_org(request: Request) -> tuple[str | None, str | None]:
    from api.core.jwt import decode_token
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None, None
    try:
        payload = decode_token(auth.split(" ", 1)[1], expected_type="access")
        return payload.get("sub"), payload.get("org_id")
    except Exception:
        return None, None


def _check_repeated_401(ip: str) -> int:
    now = time.time()
    cutoff = now - 60
    _auth_fail_windows[ip] = [t for t in _auth_fail_windows[ip] if t > cutoff]
    _auth_fail_windows[ip].append(now)
    return len(_auth_fail_windows[ip])


async def _write_log_and_flags(
    request_id: str, user_id: str | None, org_id: str | None,
    method: str, path: str, status_code: int, latency_ms: int,
    ip: str, user_agent: str | None, is_error: bool,
) -> None:
    try:
        from api.core.database import AsyncSessionLocal
        from sqlalchemy import text
        meta_json = json.dumps({"user_agent": user_agent})
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    INSERT INTO request_logs
                        (id, request_id, user_id, org_id, method, path,
                         status_code, latency_ms, ip, meta, is_error, created_at)
                    VALUES
                        (:id, :request_id, :user_id, :org_id, :method, :path,
                         :status_code, :latency_ms, :ip, CAST(:meta AS jsonb), :is_error, NOW())
                """),
                {"id": str(uuid.uuid4()), "request_id": request_id,
                 "user_id": user_id, "org_id": org_id, "method": method,
                 "path": path, "status_code": status_code, "latency_ms": latency_ms,
                 "ip": ip, "meta": meta_json, "is_error": is_error},
            )
            await db.commit()
    except Exception as exc:
        print(f"[request_logger] write error (non-fatal): {exc}")

    try:
        from api.services.monitor_service import (
            flag_http_5xx, flag_slow_request, flag_repeated_401)

        if status_code >= 500:
            await flag_http_5xx(request_id, user_id, org_id, path, status_code, latency_ms)

        if latency_ms > _SLOW_THRESHOLD_MS:
            await flag_slow_request(request_id, user_id, org_id, path, latency_ms)

        if status_code == 401:
            count = _check_repeated_401(ip)
            if count >= _REPEATED_401_THRESHOLD:
                await flag_repeated_401(user_id, org_id, ip, count)

    except Exception as exc:
        print(f"[request_logger] monitoring error (non-fatal): {exc}")


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.monotonic()
        response = await call_next(request)
        latency_ms = int((time.monotonic() - start) * 1000)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        user_id, org_id = _extract_user_org(request)
        asyncio.create_task(_write_log_and_flags(
            request_id=request_id, user_id=user_id, org_id=org_id,
            method=request.method, path=request.url.path,
            status_code=status_code, latency_ms=latency_ms,
            ip=_get_client_ip(request), user_agent=request.headers.get("user-agent"),
            is_error=status_code >= 500,
        ))
        return response