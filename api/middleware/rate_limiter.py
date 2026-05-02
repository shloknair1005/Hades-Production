import asyncio
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from api.core.jwt import decode_token

# ── Limits ────────────────────────────────────────────────────────────────────
# (requests allowed, window seconds)
LIMITS = {
    # keyed by (scope, endpoint_group)
    ("user", "general"):      (60,  60),
    ("user", "run_trigger"):  (10, 3600),
    ("org",  "general"):      (300, 60),
    ("org",  "run_trigger"):  (50, 3600),
    ("ip",   "public"):       (20,  60),
}

# Routes that count as "run_trigger" for rate limiting purposes
_RUN_TRIGGER_PATH_PREFIX = "/problems/"
_RUN_TRIGGER_PATH_SUFFIX = "/runs"

# Public routes — no JWT, rate limit by IP only
_PUBLIC_PREFIXES = ("/shared/", "/auth/register", "/auth/login", "/auth/refresh")

# How often to flush in-process counters to Postgres (seconds)
_FLUSH_INTERVAL = 10

# In-process sliding window state:
# key = (scope_type, scope_id, endpoint_group)
# value = list of request timestamps (float epoch seconds)
_windows: dict[tuple, list[float]] = defaultdict(list)
_flush_lock = asyncio.Lock()
_last_flush: float = 0.0


def _endpoint_group(path: str, method: str) -> str:
    if (
        method == "POST"
        and path.startswith(_RUN_TRIGGER_PATH_PREFIX)
        and path.endswith(_RUN_TRIGGER_PATH_SUFFIX)
    ):
        return "run_trigger"
    return "general"


def _is_public(path: str) -> bool:
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


def _sliding_window_check(scope_type: str, scope_id: str, endpoint_group: str) -> tuple[bool, int]:
    """
    Returns (allowed, retry_after_seconds).
    Mutates _windows in-place: prunes expired timestamps, appends current.
    """
    limit, window_secs = LIMITS.get((scope_type, endpoint_group), (60, 60))
    key = (scope_type, scope_id, endpoint_group)
    now = time.time()
    cutoff = now - window_secs

    # Prune timestamps outside the window
    _windows[key] = [t for t in _windows[key] if t > cutoff]

    if len(_windows[key]) >= limit:
        oldest = _windows[key][0]
        retry_after = int(window_secs - (now - oldest)) + 1
        return False, retry_after

    _windows[key].append(now)
    return True, 0


async def _flush_to_db() -> None:
    """
    Write current window state to rate_limit_windows table.
    Fire-and-forget — failures are logged but never raise.
    """
    global _last_flush
    now = time.time()
    if now - _last_flush < _FLUSH_INTERVAL:
        return

    async with _flush_lock:
        if now - _last_flush < _FLUSH_INTERVAL:
            return
        _last_flush = now

    try:
        from api.core.database import AsyncSessionLocal
        from sqlalchemy import text

        snapshot = {k: list(v) for k, v in _windows.items()}
        if not snapshot:
            return

        async with AsyncSessionLocal() as db:
            for (scope_type, scope_id, endpoint_group), timestamps in snapshot.items():
                if not timestamps:
                    continue
                _, window_secs = LIMITS.get((scope_type, endpoint_group), (60, 60))
                cutoff = now - window_secs
                active = [t for t in timestamps if t > cutoff]
                if not active:
                    continue

                window_start = datetime.fromtimestamp(active[0], tz=timezone.utc)
                await db.execute(
                    text("""
                        INSERT INTO rate_limit_windows
                            (id, scope_type, scope_id, endpoint_group,
                             window_start, request_count, updated_at)
                        VALUES
                            (:id, :scope_type, :scope_id, :endpoint_group,
                             :window_start, :count, NOW())
                        ON CONFLICT (scope_type, scope_id, endpoint_group, window_start)
                        DO UPDATE SET
                            request_count = EXCLUDED.request_count,
                            updated_at    = NOW()
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "scope_type": scope_type,
                        "scope_id": scope_id,
                        "endpoint_group": endpoint_group,
                        "window_start": window_start,
                        "count": len(active),
                    },
                )
            await db.commit()
    except Exception as exc:
        print(f"[rate_limiter] flush error (non-fatal): {exc}")


def _extract_user_org(request: Request) -> tuple[str | None, str | None]:
    """Decode JWT from Authorization header without raising — returns (user_id, org_id)."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None, None
    try:
        payload = decode_token(auth.split(" ", 1)[1], expected_type="access")
        return payload.get("sub"), payload.get("org_id")
    except Exception:
        return None, None


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiterMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method
        endpoint_group = _endpoint_group(path, method)
        public = _is_public(path)

        if public:
            # Public endpoints: rate limit by IP only
            ip = _get_client_ip(request)
            allowed, retry_after = _sliding_window_check("ip", ip, "public")
            if not allowed:
                return self._too_many(retry_after, "ip")
        else:
            user_id, org_id = _extract_user_org(request)

            # Per-user check
            if user_id:
                allowed, retry_after = _sliding_window_check("user", user_id, endpoint_group)
                if not allowed:
                    return self._too_many(retry_after, "user")

            # Per-org check (independent — org can be throttled even if user is fine)
            if org_id:
                allowed, retry_after = _sliding_window_check("org", org_id, endpoint_group)
                if not allowed:
                    return self._too_many(retry_after, "org")

        # Async flush to DB (non-blocking — never delays response)
        asyncio.create_task(_flush_to_db())

        return await call_next(request)

    @staticmethod
    def _too_many(retry_after: int, scope: str) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "error": "rate_limit_exceeded",
                "code": 429,
                "detail": f"{scope.capitalize()} rate limit reached. Retry after {retry_after}s.",
            },
        )
