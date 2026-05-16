"""
Monitor service — writes MonitoringFlag rows for infrastructure events.
Called from request_logger (5xx, slow, 429, repeated 401s)
and run_service (failed runs). All writes are fire-and-forget.
"""
import json
from typing import Optional
from api.models.orm import MonitoringFlag, FlagType, FlagSeverity


async def flag_http_5xx(request_id: str, user_id: Optional[str], org_id: Optional[str],
                        path: str, status_code: int, latency_ms: int) -> None:
    await _write(FlagType.http_5xx, FlagSeverity.high, user_id=user_id, org_id=org_id,
                 request_id=request_id, meta={"path": path, "status_code": status_code, "latency_ms": latency_ms})


async def flag_slow_request(request_id: str, user_id: Optional[str], org_id: Optional[str],
                            path: str, latency_ms: int) -> None:
    await _write(FlagType.slow_request, FlagSeverity.medium, user_id=user_id, org_id=org_id,
                 request_id=request_id, meta={"path": path, "latency_ms": latency_ms})


async def flag_rate_limit(request_id: str, user_id: Optional[str], org_id: Optional[str],
                          path: str, scope: str) -> None:
    await _write(FlagType.rate_limit, FlagSeverity.medium, user_id=user_id, org_id=org_id,
                 request_id=request_id, meta={"path": path, "scope": scope})


async def flag_repeated_401(user_id: Optional[str], org_id: Optional[str],
                            ip: str, count: int) -> None:
    await _write(FlagType.repeated_401, FlagSeverity.high, user_id=user_id, org_id=org_id,
                 meta={"ip": ip, "count_in_window": count})


async def flag_failed_run(run_id: str, user_id: Optional[str],
                          org_id: Optional[str], error: str) -> None:
    await _write(FlagType.failed_run, FlagSeverity.high, user_id=user_id, org_id=org_id,
                 run_id=run_id, meta={"error": error[:500]})


async def _write(flag_type: FlagType, severity: FlagSeverity,
                 user_id: Optional[str] = None, org_id: Optional[str] = None,
                 run_id: Optional[str] = None, request_id: Optional[str] = None,
                 meta: Optional[dict] = None) -> None:
    try:
        from api.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            db.add(MonitoringFlag(flag_type=flag_type, severity=severity,
                                  user_id=user_id, org_id=org_id, run_id=run_id,
                                  request_id=request_id, meta=meta))
            await db.commit()

        if severity in (FlagSeverity.high, FlagSeverity.critical):
            from api.services.email_service import send_flag_alert
            await send_flag_alert(flag_type=flag_type.value, severity=severity.value,
                                  user_id=user_id, org_id=org_id,
                                  detail=json.dumps(meta or {}))
    except Exception as exc:
        print(f"[monitor_service] flag write error (non-fatal): {exc}")