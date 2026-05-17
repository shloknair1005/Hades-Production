"""
Background scheduler — replaces pg_cron for Windows compatibility.
Uses APScheduler with AsyncIOScheduler so all jobs share the FastAPI
event loop and can open their own DB sessions via AsyncSessionLocal.

Every job writes a SchedulerLog row with:
  - summary      : one-liner shown collapsed in the monitoring UI
  - detail       : full JSON list of affected rows (IDs, table, reason, timestamps)
  - status       : success | error | skipped
  - duration_ms  : wall-clock time for the job

Started and stopped inside the FastAPI lifespan in main.py.
"""
import time
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from api.core.database import AsyncSessionLocal
from api.models.orm import SchedulerLog, SchedulerJobStatus

scheduler = AsyncIOScheduler(timezone="UTC")


# ── Helper ────────────────────────────────────────────────────────────────────

async def _write_log(
    db,
    job_id: str,
    status: SchedulerJobStatus,
    rows_affected: int,
    summary: str,
    detail: dict | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    db.add(SchedulerLog(
        job_id=job_id,
        status=status,
        rows_affected=rows_affected,
        summary=summary,
        detail=detail,
        error_message=error_message,
        duration_ms=duration_ms,
    ))
    await db.commit()


# ── Job definitions ───────────────────────────────────────────────────────────

async def purge_standard_logs() -> None:
    """Delete request_logs rows older than 30 days where is_error = false."""
    t0 = time.monotonic()
    async with AsyncSessionLocal() as db:
        try:
            preview = await db.execute(text("""
                SELECT id, path, status_code, user_id, created_at
                FROM request_logs
                WHERE is_error = FALSE
                  AND created_at < NOW() - INTERVAL '30 days'
                ORDER BY created_at
                LIMIT 500
            """))
            rows = preview.fetchall()

            result = await db.execute(text("""
                DELETE FROM request_logs
                WHERE is_error = FALSE
                  AND created_at < NOW() - INTERVAL '30 days'
            """))
            n = result.rowcount
            duration_ms = int((time.monotonic() - t0) * 1000)

            detail_rows = [
                {
                    "id": str(r.id),
                    "table": "request_logs",
                    "path": r.path,
                    "status_code": r.status_code,
                    "user_id": str(r.user_id) if r.user_id else None,
                    "age_days": (datetime.now(timezone.utc) - r.created_at.replace(tzinfo=timezone.utc)).days
                    if r.created_at else None,
                    "reason": "Standard log older than 30 days",
                }
                for r in rows
            ]
            extra = n - len(detail_rows)

            status = SchedulerJobStatus.skipped if n == 0 else SchedulerJobStatus.success
            summary = (
                f"Deleted {n} standard request log{'s' if n != 1 else ''} older than 30 days"
                if n else "No standard logs to purge"
            )
            await _write_log(
                db, "purge_standard_logs", status, n, summary,
                detail={
                    "rows": detail_rows,
                    "truncated": extra > 0,
                    "total_deleted": n,
                    "threshold": "30 days",
                    "note": f"{extra} additional rows deleted beyond preview cap of 500" if extra > 0 else None,
                },
                duration_ms=duration_ms,
            )
            print(f"[scheduler] purge_standard_logs: deleted {n} rows")
        except Exception as exc:
            await _write_log(db, "purge_standard_logs", SchedulerJobStatus.error, 0,
                             f"Error: {str(exc)[:200]}", error_message=str(exc))
            print(f"[scheduler] purge_standard_logs ERROR: {exc}")


async def purge_error_logs() -> None:
    """Delete request_logs rows older than 60 days where is_error = true."""
    t0 = time.monotonic()
    async with AsyncSessionLocal() as db:
        try:
            preview = await db.execute(text("""
                SELECT id, path, status_code, user_id, created_at
                FROM request_logs
                WHERE is_error = TRUE
                  AND created_at < NOW() - INTERVAL '60 days'
                ORDER BY created_at
                LIMIT 500
            """))
            rows = preview.fetchall()

            result = await db.execute(text("""
                DELETE FROM request_logs
                WHERE is_error = TRUE
                  AND created_at < NOW() - INTERVAL '60 days'
            """))
            n = result.rowcount
            duration_ms = int((time.monotonic() - t0) * 1000)

            detail_rows = [
                {
                    "id": str(r.id),
                    "table": "request_logs",
                    "path": r.path,
                    "status_code": r.status_code,
                    "user_id": str(r.user_id) if r.user_id else None,
                    "age_days": (datetime.now(timezone.utc) - r.created_at.replace(tzinfo=timezone.utc)).days
                    if r.created_at else None,
                    "reason": "Error log older than 60 days",
                }
                for r in rows
            ]
            extra = n - len(detail_rows)

            status = SchedulerJobStatus.skipped if n == 0 else SchedulerJobStatus.success
            summary = (
                f"Deleted {n} error request log{'s' if n != 1 else ''} older than 60 days"
                if n else "No error logs to purge"
            )
            await _write_log(
                db, "purge_error_logs", status, n, summary,
                detail={
                    "rows": detail_rows,
                    "truncated": extra > 0,
                    "total_deleted": n,
                    "threshold": "60 days",
                    "note": f"{extra} additional rows deleted beyond preview cap of 500" if extra > 0 else None,
                },
                duration_ms=duration_ms,
            )
            print(f"[scheduler] purge_error_logs: deleted {n} rows")
        except Exception as exc:
            await _write_log(db, "purge_error_logs", SchedulerJobStatus.error, 0,
                             f"Error: {str(exc)[:200]}", error_message=str(exc))
            print(f"[scheduler] purge_error_logs ERROR: {exc}")


async def purge_rate_limit_windows() -> None:
    """Delete rate_limit_windows rows older than 2 hours."""
    t0 = time.monotonic()
    async with AsyncSessionLocal() as db:
        try:
            preview = await db.execute(text("""
                SELECT id, scope_type, scope_id, endpoint_group, window_start, request_count
                FROM rate_limit_windows
                WHERE window_start < NOW() - INTERVAL '2 hours'
                ORDER BY window_start
                LIMIT 200
            """))
            rows = preview.fetchall()

            result = await db.execute(text("""
                DELETE FROM rate_limit_windows
                WHERE window_start < NOW() - INTERVAL '2 hours'
            """))
            n = result.rowcount
            duration_ms = int((time.monotonic() - t0) * 1000)

            detail_rows = [
                {
                    "id": str(r.id),
                    "table": "rate_limit_windows",
                    "scope_type": r.scope_type,
                    "scope_id": r.scope_id,
                    "endpoint_group": r.endpoint_group,
                    "window_start": r.window_start.isoformat() if r.window_start else None,
                    "request_count": r.request_count,
                    "reason": "Rate limit window expired (older than 2 hours)",
                }
                for r in rows
            ]
            extra = n - len(detail_rows)

            status = SchedulerJobStatus.skipped if n == 0 else SchedulerJobStatus.success
            summary = (
                f"Purged {n} stale rate limit window{'s' if n != 1 else ''} (>2h old)"
                if n else "No stale rate limit windows to purge"
            )
            await _write_log(
                db, "purge_rate_limit_windows", status, n, summary,
                detail={
                    "rows": detail_rows,
                    "truncated": extra > 0,
                    "total_deleted": n,
                    "threshold": "2 hours",
                    "note": f"{extra} additional rows deleted beyond preview cap of 200" if extra > 0 else None,
                },
                duration_ms=duration_ms,
            )
            print(f"[scheduler] purge_rate_limit_windows: deleted {n} rows")
        except Exception as exc:
            await _write_log(db, "purge_rate_limit_windows", SchedulerJobStatus.error, 0,
                             f"Error: {str(exc)[:200]}", error_message=str(exc))
            print(f"[scheduler] purge_rate_limit_windows ERROR: {exc}")


async def aggregate_trust_scores() -> None:
    """
    Roll up yesterday's feedback into agent_trust_scores.
    Idempotent — ON CONFLICT DO NOTHING means reruns are safe.
    """
    t0 = time.monotonic()
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(text("""
                INSERT INTO agent_trust_scores (
                    id, org_id, agent_name, period,
                    times_chosen, total_runs, avg_rating, computed_at
                )
                SELECT
                    gen_random_uuid(), p.org_id, f.chosen_agent,
                    CURRENT_DATE - INTERVAL '1 day',
                    COUNT(*) FILTER (WHERE f.chosen_agent IS NOT NULL),
                    COUNT(*), AVG(f.rating), NOW()
                FROM feedback f
                JOIN decision_runs dr ON dr.id = f.run_id
                JOIN problems p       ON p.id  = dr.problem_id
                WHERE dr.created_at >= CURRENT_DATE - INTERVAL '1 day'
                  AND dr.created_at <  CURRENT_DATE
                  AND f.chosen_agent IS NOT NULL
                GROUP BY p.org_id, f.chosen_agent
                ON CONFLICT DO NOTHING
            """))
            n = result.rowcount
            duration_ms = int((time.monotonic() - t0) * 1000)

            # Fetch what exists for yesterday's period
            inserted = await db.execute(text("""
                SELECT id, org_id, agent_name, times_chosen, total_runs, avg_rating
                FROM agent_trust_scores
                WHERE period = CURRENT_DATE - INTERVAL '1 day'
                ORDER BY org_id, agent_name
            """))
            inserted_rows = inserted.fetchall()

            status = SchedulerJobStatus.skipped if n == 0 else SchedulerJobStatus.success
            summary = (
                f"Aggregated trust scores for {n} org-agent pair{'s' if n != 1 else ''} (yesterday)"
                if n else "No new trust score rows (already computed or no feedback yesterday)"
            )
            await _write_log(
                db, "aggregate_trust_scores", status, n, summary,
                detail={
                    "period": (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat(),
                    "rows": [
                        {
                            "id": str(r.id),
                            "table": "agent_trust_scores",
                            "org_id": str(r.org_id),
                            "agent_name": r.agent_name,
                            "times_chosen": r.times_chosen,
                            "total_runs": r.total_runs,
                            "avg_rating": round(float(r.avg_rating), 2) if r.avg_rating else None,
                            "reason": "Daily trust score rollup",
                        }
                        for r in inserted_rows
                    ],
                },
                duration_ms=duration_ms,
            )
            print(f"[scheduler] aggregate_trust_scores: inserted {n} rows")
        except Exception as exc:
            await _write_log(db, "aggregate_trust_scores", SchedulerJobStatus.error, 0,
                             f"Error: {str(exc)[:200]}", error_message=str(exc))
            print(f"[scheduler] aggregate_trust_scores ERROR: {exc}")

    from api.middleware.cache import invalidate_all_analytics
    invalidate_all_analytics()
    print("[scheduler] analytics cache invalidated after trust score aggregation")


async def purge_monitoring_flags() -> None:
    """Delete resolved monitoring_flags older than 90 days."""
    t0 = time.monotonic()
    async with AsyncSessionLocal() as db:
        try:
            preview = await db.execute(text("""
                SELECT id, flag_type, severity, user_id, org_id, resolved_at, created_at
                FROM monitoring_flags
                WHERE resolved = TRUE
                  AND created_at < NOW() - INTERVAL '90 days'
                ORDER BY created_at
                LIMIT 500
            """))
            rows = preview.fetchall()

            result = await db.execute(text("""
                DELETE FROM monitoring_flags
                WHERE resolved = TRUE
                  AND created_at < NOW() - INTERVAL '90 days'
            """))
            n = result.rowcount
            duration_ms = int((time.monotonic() - t0) * 1000)

            detail_rows = [
                {
                    "id": str(r.id),
                    "table": "monitoring_flags",
                    "flag_type": r.flag_type,
                    "severity": r.severity,
                    "user_id": str(r.user_id) if r.user_id else None,
                    "org_id": str(r.org_id) if r.org_id else None,
                    "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                    "age_days": (datetime.now(timezone.utc) - r.created_at.replace(tzinfo=timezone.utc)).days
                    if r.created_at else None,
                    "reason": "Resolved monitoring flag older than 90 days",
                }
                for r in rows
            ]
            extra = n - len(detail_rows)

            status = SchedulerJobStatus.skipped if n == 0 else SchedulerJobStatus.success
            summary = (
                f"Purged {n} resolved monitoring flag{'s' if n != 1 else ''} older than 90 days"
                if n else "No resolved monitoring flags to purge"
            )
            await _write_log(
                db, "purge_monitoring_flags", status, n, summary,
                detail={
                    "rows": detail_rows,
                    "truncated": extra > 0,
                    "total_deleted": n,
                    "threshold": "90 days",
                    "note": f"{extra} additional rows deleted beyond preview cap of 500" if extra > 0 else None,
                },
                duration_ms=duration_ms,
            )
            print(f"[scheduler] purge_monitoring_flags: deleted {n} rows")
        except Exception as exc:
            await _write_log(db, "purge_monitoring_flags", SchedulerJobStatus.error, 0,
                             f"Error: {str(exc)[:200]}", error_message=str(exc))
            print(f"[scheduler] purge_monitoring_flags ERROR: {exc}")


# ── Scheduler setup ───────────────────────────────────────────────────────────

def register_jobs() -> None:
    """Register all jobs. Called once from main.py lifespan."""

    scheduler.add_job(purge_standard_logs,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="purge_standard_logs", replace_existing=True, misfire_grace_time=3600)

    scheduler.add_job(purge_error_logs,
        trigger=CronTrigger(hour=2, minute=5, timezone="UTC"),
        id="purge_error_logs", replace_existing=True, misfire_grace_time=3600)

    scheduler.add_job(purge_rate_limit_windows,
        trigger=IntervalTrigger(minutes=10),
        id="purge_rate_limit_windows", replace_existing=True)

    scheduler.add_job(aggregate_trust_scores,
        trigger=CronTrigger(hour=1, minute=0, timezone="UTC"),
        id="aggregate_trust_scores", replace_existing=True, misfire_grace_time=3600)

    scheduler.add_job(purge_monitoring_flags,
        trigger=CronTrigger(hour=2, minute=15, timezone="UTC"),
        id="purge_monitoring_flags", replace_existing=True, misfire_grace_time=3600)
