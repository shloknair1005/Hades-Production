import secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from api.core.database import get_db
from api.middleware.auth import get_current_user, require_admin, require_power_or_admin
from api.middleware.cache import (
    get_shared_run, set_shared_run, invalidate_shared_run,
    get_analytics, set_analytics, invalidate_analytics,
    invalidate_all_agent_configs_for_org,
)
from api.models import orm, schemas
from api.services import auth_service, run_service


# ── Auth ──────────────────────────────────────────────────────────────────────

auth_router = APIRouter(prefix="/auth", tags=["auth"])

@auth_router.post("/register", response_model=schemas.TokenResponse, status_code=201)
async def register(req: schemas.RegisterRequest, db: AsyncSession = Depends(get_db)):
    return await auth_service.register(req, db)

@auth_router.post("/login", response_model=schemas.TokenResponse)
async def login(req: schemas.LoginRequest, db: AsyncSession = Depends(get_db)):
    return await auth_service.login(req, db)

@auth_router.post("/refresh", response_model=schemas.TokenResponse)
async def refresh(req: schemas.RefreshRequest, db: AsyncSession = Depends(get_db)):
    return await auth_service.refresh(req.refresh_token, db)

@auth_router.post("/logout", status_code=204)
async def logout(req: schemas.RefreshRequest, db: AsyncSession = Depends(get_db),
                 current_user: orm.User = Depends(get_current_user)):
    await auth_service.logout(req.refresh_token, db)

@auth_router.get("/me", response_model=schemas.UserOut)
async def me(current_user: orm.User = Depends(get_current_user)):
    return current_user


# ── Problems ──────────────────────────────────────────────────────────────────

problems_router = APIRouter(prefix="/problems", tags=["problems"])

@problems_router.get("", response_model=list[schemas.ProblemOut])
async def list_problems(
    visibility: Optional[orm.Visibility] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    q = select(orm.Problem).where(
        orm.Problem.owner_id == current_user.id,
        orm.Problem.is_deleted == False,
    )
    if visibility:
        q = q.where(orm.Problem.visibility == visibility)
    q = q.order_by(desc(orm.Problem.created_at)).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return result.scalars().all()

@problems_router.post("", response_model=schemas.ProblemOut, status_code=201)
async def create_problem(
    req: schemas.ProblemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    problem = orm.Problem(
        owner_id=current_user.id,
        org_id=current_user.org_id,
        title=req.title,
        visibility=req.visibility,
    )
    db.add(problem)
    await db.flush()
    return problem

@problems_router.get("/{problem_id}", response_model=schemas.ProblemOut)
async def get_problem(
    problem_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    return await run_service.get_problem_or_404(problem_id, current_user.id, db)

@problems_router.patch("/{problem_id}", response_model=schemas.ProblemOut)
async def update_problem(
    problem_id: str,
    req: schemas.ProblemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    problem = await run_service.get_problem_or_404(problem_id, current_user.id, db)
    if problem.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can edit this problem")
    if req.title is not None:
        problem.title = req.title
    if req.visibility is not None:
        problem.visibility = req.visibility
    return problem

@problems_router.delete("/{problem_id}", status_code=204)
async def delete_problem(
    problem_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    problem = await run_service.get_problem_or_404(problem_id, current_user.id, db)
    if problem.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete this problem")
    problem.is_deleted = True


# ── Runs ──────────────────────────────────────────────────────────────────────

runs_router = APIRouter(tags=["runs"])

@problems_router.post("/{problem_id}/runs", response_model=schemas.RunOut, status_code=202)
async def trigger_run(
    problem_id: str,
    req: schemas.RunCreate,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    return await run_service.create_run(problem_id, req, current_user.id, current_user.org_id, db)

@problems_router.get("/{problem_id}/runs", response_model=list[schemas.RunOut])
async def list_runs(
    problem_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    return await run_service.list_runs(problem_id, current_user.id, db)

@runs_router.get("/runs/{run_id}", response_model=schemas.RunOut)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    return await run_service.get_run_or_404(run_id, current_user.id, db)

@runs_router.get("/runs/{run_id}/agents", response_model=list[schemas.AgentOutputOut])
async def get_agent_outputs(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    return await run_service.get_agent_outputs(run_id, current_user.id, db)


# ── Feedback ──────────────────────────────────────────────────────────────────

feedback_router = APIRouter(tags=["feedback"])

@feedback_router.post("/runs/{run_id}/feedback", response_model=schemas.FeedbackOut, status_code=201)
async def submit_feedback(
    run_id: str,
    req: schemas.FeedbackCreate,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    await run_service.get_run_or_404(run_id, current_user.id, db)

    existing = await db.execute(
        select(orm.Feedback).where(orm.Feedback.run_id == run_id, orm.Feedback.user_id == current_user.id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Feedback already submitted for this run")

    fb = orm.Feedback(run_id=run_id, user_id=current_user.id, **req.model_dump())
    db.add(fb)
    db.add(orm.UsageEvent(user_id=current_user.id, org_id=current_user.org_id,
                          event_type="feedback_submitted", run_id=run_id))
    await db.flush()

    # Feedback changes analytics — bust the org analytics cache
    invalidate_analytics(current_user.org_id)

    return fb

@feedback_router.get("/runs/{run_id}/feedback", response_model=schemas.FeedbackOut)
async def get_my_feedback(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    result = await db.execute(
        select(orm.Feedback).where(orm.Feedback.run_id == run_id, orm.Feedback.user_id == current_user.id)
    )
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="No feedback found")
    return fb


# ── Comments ──────────────────────────────────────────────────────────────────

comments_router = APIRouter(tags=["comments"])

@comments_router.get("/runs/{run_id}/comments", response_model=list[schemas.CommentOut])
async def list_comments(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    await run_service.get_run_or_404(run_id, current_user.id, db)
    result = await db.execute(
        select(orm.Comment).where(orm.Comment.run_id == run_id).order_by(orm.Comment.created_at)
    )
    return result.scalars().all()

@comments_router.post("/runs/{run_id}/comments", response_model=schemas.CommentOut, status_code=201)
async def post_comment(
    run_id: str,
    req: schemas.CommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    await run_service.get_run_or_404(run_id, current_user.id, db)
    comment = orm.Comment(run_id=run_id, user_id=current_user.id, body=req.body, parent_id=req.parent_id)
    db.add(comment)
    await db.flush()
    return comment

@comments_router.patch("/comments/{comment_id}", response_model=schemas.CommentOut)
async def edit_comment(
    comment_id: str,
    req: schemas.CommentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    result = await db.execute(select(orm.Comment).where(orm.Comment.id == comment_id))
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot edit another user's comment")
    comment.body = req.body
    comment.edited_at = datetime.now(timezone.utc)
    return comment

@comments_router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    result = await db.execute(select(orm.Comment).where(orm.Comment.id == comment_id))
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != current_user.id and current_user.role != orm.UserRole.admin:
        raise HTTPException(status_code=403, detail="Cannot delete another user's comment")
    await db.delete(comment)


# ── Sharing ───────────────────────────────────────────────────────────────────

shares_router = APIRouter(tags=["sharing"])

@shares_router.post("/runs/{run_id}/share", response_model=schemas.ShareOut, status_code=201)
async def create_share(
    run_id: str,
    req: schemas.ShareCreate,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    await run_service.get_run_or_404(run_id, current_user.id, db)
    share = orm.DecisionShare(
        run_id=run_id,
        shared_by=current_user.id,
        shared_with_org=current_user.org_id if not req.is_public_link else None,
        share_token=secrets.token_urlsafe(32),
        is_public_link=req.is_public_link,
        expires_at=req.expires_at,
    )
    db.add(share)
    await db.flush()
    return share

@shares_router.get("/runs/{run_id}/share", response_model=schemas.ShareOut)
async def get_share(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    result = await db.execute(select(orm.DecisionShare).where(orm.DecisionShare.run_id == run_id))
    share = result.scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=404, detail="No share found for this run")
    return share

@shares_router.delete("/runs/{run_id}/share", status_code=204)
async def revoke_share(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    result = await db.execute(select(orm.DecisionShare).where(orm.DecisionShare.run_id == run_id))
    share = result.scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=404, detail="No share found")
    # Immediately bust the cache so the token stops working
    invalidate_shared_run(share.share_token)
    await db.delete(share)

@shares_router.get("/shared/{share_token}", response_model=schemas.RunOut)
async def view_shared(share_token: str, db: AsyncSession = Depends(get_db)):
    # ── Cache read ────────────────────────────────────────────────────────────
    cached_run = get_shared_run(share_token)
    if cached_run is not None:
        return cached_run

    # ── DB fetch on cache miss ────────────────────────────────────────────────
    result = await db.execute(
        select(orm.DecisionShare).where(orm.DecisionShare.share_token == share_token)
    )
    share = result.scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    if share.expires_at and share.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Share link has expired")

    run_result = await db.execute(select(orm.DecisionRun).where(orm.DecisionRun.id == share.run_id))
    run = run_result.scalar_one()

    # Only cache completed runs — pending/running runs change frequently
    if run.status == orm.RunStatus.done:
        set_shared_run(share_token, run)

    return run


# ── Analytics ─────────────────────────────────────────────────────────────────

analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])

@analytics_router.get("/me", response_model=schemas.PersonalAnalyticsOut)
async def personal_analytics(
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    # Personal analytics cached per user (stored under org_id=user_id key)
    cache_key = f"personal:{current_user.id}"
    cached = get_analytics(current_user.id, "me")
    if cached is not None:
        return cached

    total = await db.scalar(
        select(func.count()).select_from(orm.DecisionRun).where(orm.DecisionRun.triggered_by == current_user.id)
    )
    avg_r = await db.scalar(
        select(func.avg(orm.Feedback.rating)).where(orm.Feedback.user_id == current_user.id)
    )
    top_agent = await db.scalar(
        select(orm.Feedback.chosen_agent)
        .where(orm.Feedback.user_id == current_user.id, orm.Feedback.chosen_agent.isnot(None))
        .group_by(orm.Feedback.chosen_agent)
        .order_by(func.count().desc())
        .limit(1)
    )
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0)
    runs_month = await db.scalar(
        select(func.count()).select_from(orm.DecisionRun).where(
            orm.DecisionRun.triggered_by == current_user.id,
            orm.DecisionRun.created_at >= month_start,
        )
    )
    out = schemas.PersonalAnalyticsOut(
        total_runs=total or 0,
        avg_rating_given=round(float(avg_r), 2) if avg_r else None,
        most_trusted_agent=top_agent,
        runs_this_month=runs_month or 0,
    )
    set_analytics(current_user.id, "me", out)
    return out

@analytics_router.get("/org", response_model=schemas.OrgAnalyticsOut)
async def org_analytics(
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    cached = get_analytics(current_user.org_id, "org")
    if cached is not None:
        return cached

    total = await db.scalar(
        select(func.count()).select_from(orm.DecisionRun)
        .join(orm.Problem, orm.DecisionRun.problem_id == orm.Problem.id)
        .where(orm.Problem.org_id == current_user.org_id)
    )
    active_users = await db.scalar(
        select(func.count(func.distinct(orm.DecisionRun.triggered_by)))
        .select_from(orm.DecisionRun)
        .join(orm.Problem, orm.DecisionRun.problem_id == orm.Problem.id)
        .where(orm.Problem.org_id == current_user.org_id)
    )
    avg_r = await db.scalar(
        select(func.avg(orm.Feedback.rating))
        .join(orm.DecisionRun, orm.Feedback.run_id == orm.DecisionRun.id)
        .join(orm.Problem, orm.DecisionRun.problem_id == orm.Problem.id)
        .where(orm.Problem.org_id == current_user.org_id)
    )
    top_agent = await db.scalar(
        select(orm.AgentTrustScore.agent_name)
        .where(orm.AgentTrustScore.org_id == current_user.org_id)
        .order_by(desc(orm.AgentTrustScore.times_chosen))
        .limit(1)
    )
    out = schemas.OrgAnalyticsOut(
        total_runs=total or 0,
        active_users=active_users or 0,
        most_trusted_agent=top_agent,
        avg_rating=round(float(avg_r), 2) if avg_r else None,
    )
    set_analytics(current_user.org_id, "org", out)
    return out

@analytics_router.get("/org/agents", response_model=list[schemas.AgentTrustOut])
async def org_agent_trust(
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(get_current_user),
):
    cached = get_analytics(current_user.org_id, "org_agents")
    if cached is not None:
        return cached

    result = await db.execute(
        select(orm.AgentTrustScore)
        .where(orm.AgentTrustScore.org_id == current_user.org_id)
        .order_by(desc(orm.AgentTrustScore.period), orm.AgentTrustScore.agent_name)
    )
    scores = result.scalars().all()
    out = [
        schemas.AgentTrustOut(
            agent_name=s.agent_name,
            period=str(s.period),
            times_chosen=s.times_chosen,
            total_runs=s.total_runs,
            trust_rate=round(s.times_chosen / s.total_runs, 3) if s.total_runs else None,
            avg_rating=round(float(s.avg_rating), 2) if s.avg_rating else None,
        )
        for s in scores
    ]
    set_analytics(current_user.org_id, "org_agents", out)
    return out


# ── Admin ─────────────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/admin", tags=["admin"])

@admin_router.get("/users", response_model=list[schemas.UserOut])
async def admin_list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: orm.User = Depends(require_admin),
):
    result = await db.execute(
        select(orm.User).offset((page - 1) * page_size).limit(page_size)
    )
    return result.scalars().all()

@admin_router.patch("/users/{user_id}", response_model=schemas.UserOut)
async def admin_update_user(
    user_id: str,
    req: schemas.UserAdminUpdate,
    db: AsyncSession = Depends(get_db),
    _: orm.User = Depends(require_admin),
):
    result = await db.execute(select(orm.User).where(orm.User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if req.role is not None:
        user.role = req.role
    if req.is_power_user is not None:
        user.is_power_user = req.is_power_user
    if req.org_id is not None:
        user.org_id = req.org_id
    return user

@admin_router.get("/orgs", response_model=list[schemas.OrgOut])
async def admin_list_orgs(
    db: AsyncSession = Depends(get_db),
    _: orm.User = Depends(require_admin),
):
    result = await db.execute(select(orm.Organization))
    return result.scalars().all()

@admin_router.patch("/orgs/{org_id}", response_model=schemas.OrgOut)
async def admin_update_org(
    org_id: str,
    req: schemas.OrgAdminUpdate,
    db: AsyncSession = Depends(get_db),
    _: orm.User = Depends(require_admin),
):
    result = await db.execute(select(orm.Organization).where(orm.Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    if req.name is not None:
        org.name = req.name
    if req.plan is not None:
        org.plan = req.plan
    return org

@admin_router.get("/agent-configs", response_model=list[schemas.AgentConfigOut])
async def admin_list_configs(
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(require_power_or_admin),
):
    result = await db.execute(
        select(orm.AgentConfig).where(orm.AgentConfig.org_id == current_user.org_id)
        .order_by(orm.AgentConfig.agent_name, desc(orm.AgentConfig.version))
    )
    return result.scalars().all()

@admin_router.post("/agent-configs", response_model=schemas.AgentConfigOut, status_code=201)
async def admin_create_config(
    req: schemas.AgentConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(require_power_or_admin),
):
    last_version = await db.scalar(
        select(func.max(orm.AgentConfig.version)).where(
            orm.AgentConfig.agent_name == req.agent_name,
            orm.AgentConfig.org_id == current_user.org_id,
        )
    )
    config = orm.AgentConfig(
        agent_name=req.agent_name,
        org_id=current_user.org_id,
        created_by=current_user.id,
        model=req.model,
        system_prompt=req.system_prompt,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        is_active=False,
        version=(last_version or 0) + 1,
    )
    db.add(config)
    await db.flush()
    return config

@admin_router.patch("/agent-configs/{config_id}/activate", response_model=schemas.AgentConfigOut)
async def admin_activate_config(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(require_power_or_admin),
):
    result = await db.execute(select(orm.AgentConfig).where(orm.AgentConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config or config.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="Config not found")

    prev_result = await db.execute(
        select(orm.AgentConfig).where(
            orm.AgentConfig.agent_name == config.agent_name,
            orm.AgentConfig.org_id == current_user.org_id,
            orm.AgentConfig.is_active == True,
        )
    )
    for prev in prev_result.scalars().all():
        prev.is_active = False

    config.is_active = True

    # Bust cache for every agent in this org — a new active config must be
    # picked up immediately by the next run, not after TTL expires
    invalidate_all_agent_configs_for_org(current_user.org_id)

    return config

@admin_router.get("/agent-configs/{config_id}/runs", response_model=list[schemas.RunOut])
async def admin_config_runs(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: orm.User = Depends(require_power_or_admin),
):
    result = await db.execute(
        select(orm.DecisionRun)
        .join(orm.AgentOutput, orm.AgentOutput.run_id == orm.DecisionRun.id)
        .where(orm.AgentOutput.agent_config_id == config_id)
        .distinct()
        .order_by(desc(orm.DecisionRun.created_at))
    )
    return result.scalars().all()

@admin_router.get("/analytics/platform")
async def admin_platform_analytics(
    db: AsyncSession = Depends(get_db),
    _: orm.User = Depends(require_admin),
):
    total_runs = await db.scalar(select(func.count()).select_from(orm.DecisionRun))
    total_users = await db.scalar(select(func.count()).select_from(orm.User))
    total_orgs = await db.scalar(select(func.count()).select_from(orm.Organization))
    return {"total_runs": total_runs, "total_users": total_users, "total_orgs": total_orgs}

@admin_router.get("/analytics/usage-events")
async def admin_usage_events(
    event_type: Optional[str] = None,
    org_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: orm.User = Depends(require_admin),
):
    q = select(orm.UsageEvent).order_by(desc(orm.UsageEvent.created_at))
    if event_type:
        q = q.where(orm.UsageEvent.event_type == event_type)
    if org_id:
        q = q.where(orm.UsageEvent.org_id == org_id)
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return result.scalars().all()