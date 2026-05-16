import asyncio
import time
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from api.models.orm import Problem, DecisionRun, AgentOutput, AgentConfig, UsageEvent, RunStatus, Visibility
from api.models.schemas import RunCreate
from api.middleware.cache import get_agent_config, set_agent_config
import httpx


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_active_configs(db: AsyncSession, org_id: str | None) -> dict[str, AgentConfig]:
    """
    Load active agent configs for an org.
    Checks TTL cache first (5 min) before hitting Postgres.
    Each agent cached individually so activating one only busts that entry.
    """
    agent_names = ["sales", "operations", "finance", "hades"]
    configs: dict[str, AgentConfig] = {}
    uncached: list[str] = []

    for name in agent_names:
        cached = get_agent_config(org_id, name)
        if cached is not None:
            configs[name] = cached
        else:
            uncached.append(name)

    if not uncached:
        return configs

    result = await db.execute(
        select(AgentConfig).where(
            AgentConfig.is_active == True,
            AgentConfig.org_id == org_id,
            AgentConfig.agent_name.in_(uncached),
        )
    )
    fetched = result.scalars().all()
    fetched_map = {c.agent_name: c for c in fetched}

    for name in uncached:
        if name in fetched_map:
            configs[name] = fetched_map[name]
            set_agent_config(org_id, name, fetched_map[name])

    return configs


async def _call_ollama(host: str, model: str, prompt: str, temperature: float, max_tokens: int) -> tuple[str, int]:
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{host}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        )
        resp.raise_for_status()
        data = resp.json()
    latency_ms = int((time.monotonic() - start) * 1000)
    return data.get("response", "").strip(), latency_ms


# ── Public API ────────────────────────────────────────────────────────────────

async def get_problem_or_404(problem_id: str, user_id: str, db: AsyncSession) -> Problem:
    result = await db.execute(
        select(Problem).where(Problem.id == problem_id, Problem.is_deleted == False)
    )
    problem = result.scalar_one_or_none()
    if not problem:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found")
    if problem.owner_id != user_id and problem.visibility == Visibility.private:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return problem


async def create_run(
    problem_id: str,
    req: RunCreate,
    user_id: str,
    org_id: str | None,
    db: AsyncSession,
    request_id: str | None = None,
) -> DecisionRun:
    from api.services.prompt_classifier import classify_prompt

    problem = await get_problem_or_404(problem_id, user_id, db)

    # ── Classify prompt before creating run ───────────────────────────────────
    classification = await classify_prompt(
        prompt=req.problem_text,
        user_id=user_id,
        org_id=org_id,
        run_id=None,
        request_id=request_id,
    )
    if classification.blocked:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This problem was blocked by the content safety filter. "
                   "Contact your administrator if you believe this is an error.",
        )

    count_result = await db.execute(
        select(func.count()).where(DecisionRun.problem_id == problem_id)
    )
    next_version = (count_result.scalar() or 0) + 1

    run = DecisionRun(
        problem_id=problem.id,
        triggered_by=user_id,
        version=next_version,
        problem_text=req.problem_text,
        status=RunStatus.pending,
    )
    db.add(run)
    db.add(UsageEvent(user_id=user_id, org_id=org_id, event_type="run_triggered"))
    await db.flush()

    # Commit BEFORE firing background task so _execute_run finds the row
    await db.commit()

    asyncio.create_task(_execute_run(run.id, org_id, user_id))

    return run


async def get_run_or_404(run_id: str, user_id: str, db: AsyncSession) -> DecisionRun:
    result = await db.execute(select(DecisionRun).where(DecisionRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    await get_problem_or_404(run.problem_id, user_id, db)
    return run


async def list_runs(problem_id: str, user_id: str, db: AsyncSession) -> list[DecisionRun]:
    await get_problem_or_404(problem_id, user_id, db)
    result = await db.execute(
        select(DecisionRun)
        .where(DecisionRun.problem_id == problem_id)
        .order_by(DecisionRun.version.desc())
    )
    return result.scalars().all()


async def get_agent_outputs(run_id: str, user_id: str, db: AsyncSession) -> list[AgentOutput]:
    await get_run_or_404(run_id, user_id, db)
    result = await db.execute(select(AgentOutput).where(AgentOutput.run_id == run_id))
    return result.scalars().all()


# ── Background execution ──────────────────────────────────────────────────────

async def _execute_run(run_id: str, org_id: str | None, user_id: str | None = None) -> None:
    """
    Runs outside the request context in its own DB session.
    The run row is committed before this task fires so scalar_one() always finds it.
    Uses cached agent configs, falls back to hardcoded defaults if none configured.
    """
    import traceback as tb
    from api.core.database import AsyncSessionLocal
    from api.core.config import settings

    print(f"\n[execute_run] -- starting run {run_id} --")

    async with AsyncSessionLocal() as db:
        try:
            run_result = await db.execute(select(DecisionRun).where(DecisionRun.id == run_id))
            run = run_result.scalar_one()
            run.status = RunStatus.running
            await db.commit()
            print(f"[execute_run] status -> running")

            configs = await _get_active_configs(db, org_id)
            print(f"[execute_run] configs loaded: {list(configs.keys()) or 'none (using defaults)'}")

            def _cfg(name: str) -> tuple[str, str, float, int]:
                c = configs.get(name)
                if c:
                    return c.model, c.system_prompt, c.temperature, c.max_tokens
                return settings.OLLAMA_MODEL, "", 0.7, 500

            agents = [
                ("sales",      _build_sales_prompt),
                ("operations", _build_ops_prompt),
                ("finance",    _build_finance_prompt),
            ]

            state: dict = {"problem": run.problem_text}

            for agent_name, prompt_fn in agents:
                print(f"[execute_run] calling agent: {agent_name}")
                model, system_prompt, temperature, max_tokens = _cfg(agent_name)
                prompt = prompt_fn(run.problem_text, system_prompt)
                output, latency_ms = await _call_ollama(
                    settings.OLLAMA_HOST, model, prompt, temperature, max_tokens
                )
                print(f"[execute_run] {agent_name} done in {latency_ms}ms -- {len(output)} chars")
                state[agent_name] = output

                config_id = configs[agent_name].id if agent_name in configs else None
                db.add(AgentOutput(
                    run_id=run.id,
                    agent_name=agent_name,
                    agent_config_id=config_id,
                    prompt_used=prompt,
                    raw_output=output,
                    latency_ms=latency_ms,
                ))

            print(f"[execute_run] calling agent: hades")
            model, system_prompt, temperature, max_tokens = _cfg("hades")
            hades_prompt = _build_hades_prompt(state, system_prompt)
            final_output, latency_ms = await _call_ollama(
                settings.OLLAMA_HOST, model, hades_prompt, temperature, max_tokens
            )
            print(f"[execute_run] hades done in {latency_ms}ms -- {len(final_output)} chars")

            config_id = configs["hades"].id if "hades" in configs else None
            db.add(AgentOutput(
                run_id=run.id,
                agent_name="hades",
                agent_config_id=config_id,
                prompt_used=hades_prompt,
                raw_output=final_output,
                latency_ms=latency_ms,
            ))

            run.final_decision = final_output
            run.status = RunStatus.done
            run.completed_at = datetime.now(timezone.utc)
            await db.commit()
            print(f"[execute_run] -- run {run_id} DONE --\n")

        except Exception as run_exc:
            print(f"\n[execute_run] -- run {run_id} FAILED --")
            tb.print_exc()
            print("-" * 60)
            await db.rollback()
            run_result = await db.execute(select(DecisionRun).where(DecisionRun.id == run_id))
            run = run_result.scalar_one_or_none()
            if run:
                run.status = RunStatus.failed
                await db.commit()
            # Write monitoring flag for failed run
            from api.services.monitor_service import flag_failed_run
            await flag_failed_run(
                run_id=run_id,
                user_id=user_id,
                org_id=org_id,
                error=str(run_exc),
            )


# ── Prompt builders ───────────────────────────────────────────────────────────

_FORMAT_RULES = """
RESPONSE FORMAT (strictly follow):
- Give exactly 4-6 numbered points. Each point: "N. Title: One clear sentence."
- After the points, one sentence starting with "Recommendation:" that states your single clearest action.
- No preamble, no headers, no markdown, no asterisks. Plain text only.
- Be direct and brief. Total response under 200 words.
""".strip()


def _build_sales_prompt(problem: str, system_override: str) -> str:
    base = system_override or (
        "You are a Sales Strategist. Your job is to identify the highest-impact commercial moves. "
        "Focus on revenue, customers, and market position. Be direct and opinionated."
    )
    return f"{base}\n\n{_FORMAT_RULES}\n\nProblem: {problem}\n\nYour analysis:"


def _build_ops_prompt(problem: str, system_override: str) -> str:
    base = system_override or (
        "You are an Operations Strategist. Your job is to identify execution risks and scalability gaps. "
        "Focus on process, systems, and delivery. Call out what will break before it does."
    )
    return f"{base}\n\n{_FORMAT_RULES}\n\nProblem: {problem}\n\nYour analysis:"


def _build_finance_prompt(problem: str, system_override: str) -> str:
    base = system_override or (
        "You are a Financial Strategist (CFA, CA). Your job is to protect cash flow, margins, and manage risk. "
        "Focus on numbers, unit economics, and financial exposure. Be precise."
    )
    return f"{base}\n\n{_FORMAT_RULES}\n\nProblem: {problem}\n\nYour analysis:"


_HADES_FORMAT_RULES = """
RESPONSE FORMAT (strictly follow):
- Give exactly 4-5 numbered points synthesising all three perspectives. Each point: "N. Title: One decisive sentence."
- After the points, one sentence starting with "Verdict:" that delivers the final, unambiguous decision.
- No preamble, no headers, no markdown, no asterisks. Plain text only.
- Be authoritative and brief. Total response under 180 words.
""".strip()


def _build_hades_prompt(state: dict, system_override: str) -> str:
    base = system_override or (
        "You are Hades, the final arbiter. You have heard from Sales, Operations, and Finance. "
        "Synthesise their views into a single decisive verdict. Do not hedge."
    )
    return (
        f"{base}\n\n{_HADES_FORMAT_RULES}\n\n"
        f"Problem:\n{state.get('problem', '')}\n\n"
        f"Sales Strategist:\n{state.get('sales', '')}\n\n"
        f"Operations Strategist:\n{state.get('operations', '')}\n\n"
        f"Finance Strategist:\n{state.get('finance', '')}\n\n"
        f"Your verdict:"
    )