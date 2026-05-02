import re
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Routes that trigger the LLM pipeline — much longer timeout allowed
_SLOW_ROUTES = re.compile(r"^/problems/[^/]+/runs$")

# Known bad user-agent substrings — block on public endpoints only
_BAD_UA = {"zgrab", "masscan", "nuclei", "sqlmap", "nikto", "nmap"}

# Share token format: urlsafe_b64 produced by secrets.token_urlsafe(32) = 43 chars
_SHARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,60}$")

MAX_BODY_BYTES = 512 * 1024          # 512 KB
TIMEOUT_STANDARD = 30.0              # seconds
TIMEOUT_RUN_TRIGGER = 130.0          # covers the async Ollama pipeline


class PayloadGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # ── 1. Body size check ────────────────────────────────────────────────
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "payload_too_large",
                    "code": 413,
                    "detail": f"Request body must be under {MAX_BODY_BYTES // 1024} KB.",
                },
            )

        # ── 2. Public endpoint abuse checks (/shared/{token}) ─────────────────
        path = request.url.path
        if path.startswith("/shared/"):
            token = path.split("/shared/", 1)[-1]

            # Token format pre-screen — reject garbage before any DB hit
            if not _SHARE_TOKEN_RE.match(token):
                return JSONResponse(
                    status_code=400,
                    content={"error": "invalid_share_token", "code": 400,
                             "detail": "Malformed share token."},
                )

            # Block known scanner/bot user-agents on the public endpoint
            ua = request.headers.get("user-agent", "").lower()
            if any(bad in ua for bad in _BAD_UA):
                return JSONResponse(
                    status_code=403,
                    content={"error": "forbidden", "code": 403,
                             "detail": "Automated access is not permitted."},
                )

        # ── 3. Request timeout ────────────────────────────────────────────────
        timeout = (
            TIMEOUT_RUN_TRIGGER
            if _SLOW_ROUTES.match(path) and request.method == "POST"
            else TIMEOUT_STANDARD
        )

        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start

        if elapsed > timeout:
            # Response already sent — we can only log here, not abort.
            # Real timeout enforcement requires running uvicorn behind a
            # reverse proxy (nginx/caddy) with its own timeout setting.
            pass

        return response
