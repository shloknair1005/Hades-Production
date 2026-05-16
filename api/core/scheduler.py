"""
Background scheduler — replaces pg_cron for Windows compatibility.
Uses APScheduler with AsyncIOScheduler so all jobs share the FastAPI
event loop and can open their own DB sessions via AsyncSessionLocal.

Started and stopped inside the FastAPI lifespan in main.py.
"""
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from api.core.database import AsyncSessionLocal

scheduler = AsyncIOScheduler(timezone="UTC")


# ── Job definitions ───────────────────────────────────────────────────────────

async def purge_standard_logs() -> None:
    """Delete request_logs rows older than 30 days where is_error = false."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                DELETE FROM request_logs
                WHERE is_error = FALSE
                  AND created_at < NOW() - INTERVAL '30 days'
            """)
        )
        await db.commit()
        print(f"[scheduler] purge_standard_logs: deleted {result.rowcount} rows")


async def purge_error_logs() -> None:
    """Delete request_logs rows older than 60 days where is_error = true."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                DELETE FROM request_logs
                WHERE is_error = TRUE
                  AND created_at < NOW() - INTERVAL '60 days'
            """)
        )
        await db.commit()
        print(f"[scheduler] purge_error_logs: deleted {result.rowcount} rows")


async def purge_rate_limit_windows() -> None:
    """Delete rate_limit_windows rows older than 2 hours — stale windows are irrelevant."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                DELETE FROM rate_limit_windows
                WHERE window_start < NOW() - INTERVAL '2 hours'
            """)
        )
        await db.commit()
        print(f"[scheduler] purge_rate_limit_windows: deleted {result.rowcount} rows")


async def aggregate_trust_scores() -> None:
    """
    Roll up yesterday's feedback into agent_trust_scores.
    One row per (org_id, agent_name, date).
    Idempotent — ON CONFLICT DO NOTHING means reruns are safe.
    After writing, busts analytics cache so dashboards reflect new data immediately.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                INSERT INTO agent_trust_scores (
                    id,
                    org_id,
                    agent_name,
                    period,
                    times_chosen,
                    total_runs,
                    avg_rating,
                    computed_at
                )
                SELECT
                    gen_random_uuid(),
                    p.org_id,
                    f.chosen_agent,
                    CURRENT_DATE - INTERVAL '1 day',
                    COUNT(*) FILTER (WHERE f.chosen_agent IS NOT NULL),
                    COUNT(*),
                    AVG(f.rating),
                    NOW()
                FROM feedback f
                JOIN decision_runs dr ON dr.id = f.run_id
                JOIN problems p       ON p.id  = dr.problem_id
                WHERE dr.created_at >= CURRENT_DATE - INTERVAL '1 day'
                  AND dr.created_at <  CURRENT_DATE
                  AND f.chosen_agent IS NOT NULL
                GROUP BY p.org_id, f.chosen_agent
                ON CONFLICT DO NOTHING
            """)
        )
        await db.commit()
        print(f"[scheduler] aggregate_trust_scores: inserted {result.rowcount} rows")

    # Bust analytics cache for all orgs — trust scores changed
    from api.middleware.cache import invalidate_all_analytics
    invalidate_all_analytics()
    print("[scheduler] analytics cache invalidated after trust score aggregation")


async def purge_monitoring_flags() -> None:
    """Delete resolved monitoring_flags older than 90 days. Unresolved flags are never auto-purged."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                DELETE FROM monitoring_flags
                WHERE resolved = TRUE
                  AND created_at < NOW() - INTERVAL '90 days'
            """)
        )
        await db.commit()
        print(f"[scheduler] purge_monitoring_flags: deleted {result.rowcount} rows")


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

    # Purge resolved monitoring flags older than 90 days
    scheduler.add_job(purge_monitoring_flags,
        trigger=CronTrigger(hour=2, minute=15, timezone="UTC"),
        id="purge_monitoring_flags", replace_existing=True, misfire_grace_time=3600)

    # Purge stale rate limit windows — every 10 minutes
    scheduler.add_job(
        purge_rate_limit_windows,
        trigger=IntervalTrigger(minutes=10),
        id="purge_rate_limit_windows",
        replace_existing=True,
    )

    # Aggregate trust scores — daily at 01:00 UTC (before purge jobs)
    scheduler.add_job(
        aggregate_trust_scores,
        trigger=CronTrigger(hour=1, minute=0, timezone="UTC"),
        id="aggregate_trust_scores",
        replace_existing=True,
        misfire_grace_time=3600,
    )