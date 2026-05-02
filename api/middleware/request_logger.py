import asyncio
import json
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_STRIP_HEADERS = {"authorization", "cookie", "x-api-key"}


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


async def _write_log(
    request_id: str,
    user_id: str | None,
    org_id: str | None,
    method: str,
    path: str,
    status_code: int,
    latency_ms: int,
    ip: str,
    user_agent: str | None,
    is_error: bool,
) -> None:
    """Fire-and-forget coroutine — never blocks the HTTP response."""
    try:
        from api.core.database import AsyncSessionLocal
        from sqlalchemy import text

        # Serialize meta as a proper JSON string — CAST(:meta AS jsonb) avoids
        # the ::jsonb cast syntax which asyncpg cannot parse as a named parameter.
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
                {
                    "id":           str(uuid.uuid4()),
                    "request_id":   request_id,
                    "user_id":      user_id,
                    "org_id":       org_id,
                    "method":       method,
                    "path":         path,
                    "status_code":  status_code,
                    "latency_ms":   latency_ms,
                    "ip":           ip,
                    "meta":         meta_json,
                    "is_error":     is_error,
                },
            )
            await db.commit()
    except Exception as exc:
        print(f"[request_logger] write error (non-fatal): {exc}")


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        start = time.monotonic()
        response = await call_next(request)
        latency_ms = int((time.monotonic() - start) * 1000)

        status_code = response.status_code
        is_error = status_code >= 500

        response.headers["X-Request-ID"] = request_id

        user_id, org_id = _extract_user_org(request)

        asyncio.create_task(
            _write_log(
                request_id=request_id,
                user_id=user_id,
                org_id=org_id,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                latency_ms=latency_ms,
                ip=_get_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                is_error=is_error,
            )
        )

        return response