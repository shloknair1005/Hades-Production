"""
Centralized error handling for the Judges of Hades API.

Registers four exception handlers on the FastAPI app:
  - RequestValidationError  → 422 with per-field detail
  - HTTPException           → passthrough with standardized JSON shape
  - Exception               → 500, full traceback to Sentry, safe message to client

Every error response follows this shape:
  { "error": str, "code": int, "request_id": str, "detail": any }

The request_id is pulled from request.state (set by RequestLoggerMiddleware).
If the logger hasn't run yet (e.g. error in payload guard), a fallback UUID is generated.
"""
import traceback
import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


def _sentry_capture(exc: Exception, request: Request, user_id: str | None, org_id: str | None) -> None:
    """Send to Sentry if SDK is configured. Silently no-ops if SENTRY_DSN is unset."""
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("request_id", _request_id(request))
            scope.set_tag("path", request.url.path)
            scope.set_tag("method", request.method)
            if user_id:
                scope.set_user({"id": user_id})
            if org_id:
                scope.set_tag("org_id", org_id)
            sentry_sdk.capture_exception(exc)
    except ImportError:
        pass  # sentry_sdk not installed — skip silently
    except Exception:
        pass  # never let Sentry reporting crash the error handler


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


def register_error_handlers(app: FastAPI) -> None:

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # Pydantic validation errors — include field-level detail, safe to expose
        errors = []
        for e in exc.errors():
            errors.append({
                "field": " → ".join(str(loc) for loc in e["loc"]),
                "message": e["msg"],
                "type": e["type"],
            })
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "code": 422,
                "request_id": _request_id(request),
                "detail": errors,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # Known HTTP errors (401, 403, 404, 409, etc.) — expose detail directly
        # Map status code to a machine-readable error slug
        slugs = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            410: "gone",
            413: "payload_too_large",
            422: "unprocessable",
            429: "rate_limit_exceeded",
        }
        error_slug = slugs.get(exc.status_code, f"http_{exc.status_code}")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": error_slug,
                "code": exc.status_code,
                "request_id": _request_id(request),
                "detail": exc.detail,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Unhandled 500s — full traceback to Sentry, safe generic message to client
        print(f"[error_handler] unhandled exception on {request.method} {request.url.path}")
        traceback.print_exc()

        user_id, org_id = _extract_user_org(request)
        _sentry_capture(exc, request, user_id, org_id)

        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "code": 500,
                "request_id": _request_id(request),
                "detail": "An unexpected error occurred. Quote your request_id when contacting support.",
            },
        )
