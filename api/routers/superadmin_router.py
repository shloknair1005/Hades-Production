"""
Two routers in one file:

  org_admin_router  — /org-admin/...   — admin role, own org only
  superadmin_router — /superadmin/...  — super_admin role, all orgs

Org admin endpoints cover member management, bans, role changes, and admin handoff.
Super admin endpoints cover monitoring tower, platform analytics, cross-org drilldown.
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from api.core.database import get_db
from api.middleware.auth import get_current_user, require_admin, require_super_admin
from api.models.orm import (
    User, UserRole, Organization, DecisionRun, Problem,
    AgentOutput, Feedback, MonitoringFlag, BannedUser,
    FlagType, FlagSeverity, UsageEvent, SchedulerLog, SchedulerJobStatus,
)
from api.models.schemas import UserOut, OrgOut
from pydantic import BaseModel


# ── Schemas ───────────────────────────────────────────────────────────────────

class BanRequest(BaseModel):
    reason: Optional[str] = None

class RoleChangeRequest(BaseModel):
    role: UserRole

class HandoffRequest(BaseModel):
    target_user_id: str   # member in same org who receives admin role

class FlagResolveRequest(BaseModel):
    notes: Optional[str] = None

class MemberMetrics(BaseModel):
    user_id: str
    display_name: str
    email: str
    role: UserRole
    is_power_user: bool
    is_banned: bool
    total_runs: int
    last_active: Optional[datetime]
    model_config = {"from_attributes": True}

class FlagOut(BaseModel):
    id: str
    flag_type: str
    severity: str
    user_id: Optional[str]
    user_display_name: Optional[str]   # enriched — null if user not found
    user_email: Optional[str]          # enriched
    org_id: Optional[str]
    org_name: Optional[str]            # enriched
    run_id: Optional[str]
    request_id: Optional[str]
    prompt_excerpt: Optional[str]
    classifier_reason: Optional[str]
    classifier_severity: Optional[int]
    meta: Optional[dict]
    resolved: bool
    resolved_by: Optional[str]
    resolved_at: Optional[datetime]
    created_at: datetime
    model_config = {"from_attributes": True}

class SchedulerLogOut(BaseModel):
    id: str
    job_id: str
    status: str
    rows_affected: int
    summary: str
    detail: Optional[dict]
    error_message: Optional[str]
    duration_ms: Optional[int]
    ran_at: datetime
    model_config = {"from_attributes": True}

class OrgDrilldown(BaseModel):
    org_id: str
    org_name: str
    plan: str
    total_users: int
    total_runs: int
    failed_runs: int
    unresolved_flags: int

class PlatformStats(BaseModel):
    total_orgs: int
    total_users: int
    total_runs: int
    failed_runs: int
    unresolved_flags: int
    flags_by_type: dict
    flags_by_severity: dict


# ── Org admin router ──────────────────────────────────────────────────────────

org_admin_router = APIRouter(prefix="/org-admin", tags=["org-admin"])


@org_admin_router.get("/members", response_model=list[MemberMetrics])
async def list_members(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """List all members in own org with usage metrics."""
    result = await db.execute(
        select(User).where(User.org_id == current_user.org_id)
    )
    users = result.scalars().all()

    banned_result = await db.execute(
        select(BannedUser.user_id).where(BannedUser.org_id == current_user.org_id)
    )
    banned_ids = {row[0] for row in banned_result.all()}

    members = []
    for u in users:
        run_count = await db.scalar(
            select(func.count()).select_from(DecisionRun).where(DecisionRun.triggered_by == u.id)
        ) or 0

        last_run = await db.scalar(
            select(func.max(DecisionRun.created_at)).where(DecisionRun.triggered_by == u.id)
        )

        members.append(MemberMetrics(
            user_id=u.id,
            display_name=u.display_name,
            email=u.email,
            role=u.role,
            is_power_user=u.is_power_user,
            is_banned=u.id in banned_ids,
            total_runs=run_count,
            last_active=last_run,
        ))

    return members


@org_admin_router.patch("/members/{user_id}/role", response_model=UserOut)
async def change_member_role(
    user_id: str,
    req: RoleChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Change a member's role. Admins cannot promote to admin or super_admin."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or user.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="User not found in your org")

    # Admins cannot assign admin or super_admin — only super_admin can
    if current_user.role == UserRole.admin and req.role in (UserRole.admin, UserRole.super_admin):
        raise HTTPException(status_code=403, detail="Only a super admin can assign admin or super_admin roles")

    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Use the handoff endpoint to transfer your admin role")

    user.role = req.role
    return user


@org_admin_router.post("/members/{user_id}/ban", status_code=201)
async def ban_member(
    user_id: str,
    req: BanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Ban a user in own org. Admins cannot ban themselves."""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot ban yourself")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or user.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="User not found in your org")

    existing = await db.execute(select(BannedUser).where(BannedUser.user_id == user_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already banned")

    db.add(BannedUser(user_id=user_id, banned_by=current_user.id,
                      org_id=current_user.org_id, reason=req.reason))
    return {"status": "banned"}


@org_admin_router.delete("/members/{user_id}/ban", status_code=204)
async def unban_member(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Unban a user in own org."""
    result = await db.execute(
        select(BannedUser).where(BannedUser.user_id == user_id,
                                  BannedUser.org_id == current_user.org_id)
    )
    ban = result.scalar_one_or_none()
    if not ban:
        raise HTTPException(status_code=404, detail="No active ban found")
    await db.delete(ban)


@org_admin_router.delete("/members/{user_id}", status_code=204)
async def delete_member_data(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Soft-delete all problems and runs for a user in own org."""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own data")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or user.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="User not found in your org")

    problems = await db.execute(select(Problem).where(Problem.owner_id == user_id))
    for p in problems.scalars().all():
        p.is_deleted = True


@org_admin_router.post("/handoff", response_model=UserOut)
async def handoff_admin(
    req: HandoffRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Transfer admin role to another member of the org.
    Current admin is demoted to member atomically.
    Only one admin per org at any time.
    """
    if req.target_user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You are already the admin")

    result = await db.execute(select(User).where(User.id == req.target_user_id))
    target = result.scalar_one_or_none()
    if not target or target.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="Target user not found in your org")

    if target.role == UserRole.super_admin:
        raise HTTPException(status_code=400, detail="Cannot reassign super admin role")

    # Atomic handoff
    current_user.role = UserRole.member
    target.role = UserRole.admin
    return target


@org_admin_router.get("/usage-events")
async def org_usage_events(
    event_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Usage events scoped to own org only."""
    q = (select(UsageEvent)
         .where(UsageEvent.org_id == current_user.org_id)
         .order_by(desc(UsageEvent.created_at)))
    if event_type:
        q = q.where(UsageEvent.event_type == event_type)
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return result.scalars().all()


# ── Super admin router ────────────────────────────────────────────────────────

superadmin_router = APIRouter(prefix="/superadmin", tags=["superadmin"])


@superadmin_router.get("/platform", response_model=PlatformStats)
async def platform_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    total_orgs  = await db.scalar(select(func.count()).select_from(Organization)) or 0
    total_users = await db.scalar(select(func.count()).select_from(User)) or 0
    total_runs  = await db.scalar(select(func.count()).select_from(DecisionRun)) or 0
    failed_runs = await db.scalar(
        select(func.count()).select_from(DecisionRun).where(DecisionRun.status == "failed")
    ) or 0
    unresolved = await db.scalar(
        select(func.count()).select_from(MonitoringFlag).where(MonitoringFlag.resolved == False)
    ) or 0

    by_type_rows = await db.execute(
        select(MonitoringFlag.flag_type, func.count())
        .group_by(MonitoringFlag.flag_type)
    )
    by_sev_rows = await db.execute(
        select(MonitoringFlag.severity, func.count())
        .group_by(MonitoringFlag.severity)
    )

    return PlatformStats(
        total_orgs=total_orgs, total_users=total_users,
        total_runs=total_runs, failed_runs=failed_runs,
        unresolved_flags=unresolved,
        flags_by_type={r[0]: r[1] for r in by_type_rows},
        flags_by_severity={r[0]: r[1] for r in by_sev_rows},
    )


@superadmin_router.get("/orgs", response_model=list[OrgDrilldown])
async def all_orgs_drilldown(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    orgs_result = await db.execute(select(Organization))
    orgs = orgs_result.scalars().all()
    out = []
    for org in orgs:
        users_count = await db.scalar(
            select(func.count()).select_from(User).where(User.org_id == org.id)
        ) or 0
        runs_count = await db.scalar(
            select(func.count()).select_from(DecisionRun)
            .join(Problem, DecisionRun.problem_id == Problem.id)
            .where(Problem.org_id == org.id)
        ) or 0
        failed_count = await db.scalar(
            select(func.count()).select_from(DecisionRun)
            .join(Problem, DecisionRun.problem_id == Problem.id)
            .where(Problem.org_id == org.id, DecisionRun.status == "failed")
        ) or 0
        flags_count = await db.scalar(
            select(func.count()).select_from(MonitoringFlag)
            .where(MonitoringFlag.org_id == org.id, MonitoringFlag.resolved == False)
        ) or 0
        out.append(OrgDrilldown(
            org_id=org.id, org_name=org.name, plan=org.plan,
            total_users=users_count, total_runs=runs_count,
            failed_runs=failed_count, unresolved_flags=flags_count,
        ))
    return out


@superadmin_router.get("/orgs/{org_id}/members", response_model=list[MemberMetrics])
async def org_members(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    result = await db.execute(select(User).where(User.org_id == org_id))
    users = result.scalars().all()
    banned_result = await db.execute(
        select(BannedUser.user_id).where(BannedUser.org_id == org_id)
    )
    banned_ids = {row[0] for row in banned_result.all()}
    members = []
    for u in users:
        run_count = await db.scalar(
            select(func.count()).select_from(DecisionRun).where(DecisionRun.triggered_by == u.id)
        ) or 0
        last_run = await db.scalar(
            select(func.max(DecisionRun.created_at)).where(DecisionRun.triggered_by == u.id)
        )
        members.append(MemberMetrics(
            user_id=u.id, display_name=u.display_name, email=u.email,
            role=u.role, is_power_user=u.is_power_user,
            is_banned=u.id in banned_ids,
            total_runs=run_count, last_active=last_run,
        ))
    return members


@superadmin_router.get("/users/search")
async def search_users(
    q: str = Query(..., min_length=2),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    result = await db.execute(
        select(User).where(
            User.email.ilike(f"%{q}%") | User.display_name.ilike(f"%{q}%")
        ).limit(20)
    )
    return result.scalars().all()


@superadmin_router.patch("/users/{user_id}/role", response_model=UserOut)
async def superadmin_change_role(
    user_id: str,
    req: RoleChangeRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # If promoting to admin, demote existing org admin first
    if req.role == UserRole.admin and user.org_id:
        existing_admin = await db.execute(
            select(User).where(User.org_id == user.org_id, User.role == UserRole.admin)
        )
        for ea in existing_admin.scalars().all():
            if ea.id != user_id:
                ea.role = UserRole.member

    user.role = req.role
    return user


@superadmin_router.post("/users/{user_id}/ban", status_code=201)
async def superadmin_ban(
    user_id: str,
    req: BanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    existing = await db.execute(select(BannedUser).where(BannedUser.user_id == user_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User already banned")
    db.add(BannedUser(user_id=user_id, banned_by=current_user.id,
                      org_id=user.org_id or "", reason=req.reason))
    return {"status": "banned"}


@superadmin_router.delete("/users/{user_id}/ban", status_code=204)
async def superadmin_unban(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    result = await db.execute(select(BannedUser).where(BannedUser.user_id == user_id))
    ban = result.scalar_one_or_none()
    if not ban:
        raise HTTPException(status_code=404, detail="No active ban")
    await db.delete(ban)


# ── Monitoring tower endpoints ────────────────────────────────────────────────

async def _enrich_flags(flags: list[MonitoringFlag], db: AsyncSession) -> list[FlagOut]:
    """Joins user/org names onto flags so the UI never shows bare UUIDs."""
    # Collect unique IDs to look up
    user_ids = {f.user_id for f in flags if f.user_id}
    org_ids  = {f.org_id  for f in flags if f.org_id}

    users, orgs = {}, {}
    if user_ids:
        rows = await db.execute(
            select(User.id, User.display_name, User.email).where(User.id.in_(user_ids))
        )
        for r in rows:
            users[r.id] = {"display_name": r.display_name, "email": r.email}
    if org_ids:
        rows = await db.execute(
            select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))
        )
        for r in rows:
            orgs[r.id] = r.name

    out = []
    for f in flags:
        u = users.get(f.user_id, {})
        out.append(FlagOut(
            id=f.id, flag_type=f.flag_type, severity=f.severity,
            user_id=f.user_id,
            user_display_name=u.get("display_name"),
            user_email=u.get("email"),
            org_id=f.org_id,
            org_name=orgs.get(f.org_id),
            run_id=f.run_id, request_id=f.request_id,
            prompt_excerpt=f.prompt_excerpt,
            classifier_reason=f.classifier_reason,
            classifier_severity=f.classifier_severity,
            meta=f.meta, resolved=f.resolved,
            resolved_by=f.resolved_by, resolved_at=f.resolved_at,
            created_at=f.created_at,
        ))
    return out


@superadmin_router.get("/flags", response_model=list[FlagOut])
async def list_flags(
    flag_type: Optional[str] = None,
    severity: Optional[str] = None,
    resolved: Optional[bool] = None,
    org_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    q = select(MonitoringFlag).order_by(desc(MonitoringFlag.created_at))
    if flag_type:
        q = q.where(MonitoringFlag.flag_type == flag_type)
    if severity:
        q = q.where(MonitoringFlag.severity == severity)
    if resolved is not None:
        q = q.where(MonitoringFlag.resolved == resolved)
    if org_id:
        q = q.where(MonitoringFlag.org_id == org_id)
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    flags = result.scalars().all()
    return await _enrich_flags(flags, db)


@superadmin_router.get("/flags/unresolved-count")
async def unresolved_count(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    """Lightweight polling endpoint — frontend polls this every 5s."""
    count = await db.scalar(
        select(func.count()).select_from(MonitoringFlag)
        .where(MonitoringFlag.resolved == False)
    ) or 0
    critical = await db.scalar(
        select(func.count()).select_from(MonitoringFlag)
        .where(MonitoringFlag.resolved == False, MonitoringFlag.severity == "critical")
    ) or 0
    return {"unresolved": count, "critical": critical}


@superadmin_router.patch("/flags/{flag_id}/resolve", response_model=FlagOut)
async def resolve_flag(
    flag_id: str,
    req: FlagResolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    result = await db.execute(select(MonitoringFlag).where(MonitoringFlag.id == flag_id))
    flag = result.scalar_one_or_none()
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    flag.resolved = True
    flag.resolved_by = current_user.id
    flag.resolved_at = datetime.now(timezone.utc)
    if req.notes and flag.meta:
        flag.meta = {**flag.meta, "resolution_notes": req.notes}
    elif req.notes:
        flag.meta = {"resolution_notes": req.notes}
    await db.commit()
    enriched = await _enrich_flags([flag], db)
    return enriched[0]


# ── Scheduler logs endpoint ───────────────────────────────────────────────────

@superadmin_router.get("/scheduler-logs", response_model=list[SchedulerLogOut])
async def list_scheduler_logs(
    job_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    """Return scheduler job execution history, newest first."""
    q = select(SchedulerLog).order_by(desc(SchedulerLog.ran_at))
    if job_id:
        q = q.where(SchedulerLog.job_id == job_id)
    if status:
        q = q.where(SchedulerLog.status == status)
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return result.scalars().all()
