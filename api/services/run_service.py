import asyncio
import time
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from api.models.orm import Problem, DecisionRun, AgentOutput, AgentConfig, UsageEvent, RunStatus, Visibility
from api.models.schemas import RunCreate
import httpx


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_active_configs(db: AsyncSession, org_id: str | None) -> dict[str, AgentConfig]:
    result = await db.execute(
        select(AgentConfig).where(
            AgentConfig.is_active == True,
            AgentConfig.org_id == org_id,
        )
    )
    configs = result.scalars().all()
    return {c.agent_name: c for c in configs}


async def _call_ollama(host: str, model: str, prompt: str, temperature: float, max_tokens: int) -> tuple[str, int]:
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"temperature": temperature, "num_predict": max_tokens}},
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


async def create_run(problem_id: str, req: RunCreate, user_id: str, org_id: str | None, db: AsyncSession) -> DecisionRun:
    problem = await get_problem_or_404(problem_id, user_id, db)

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
    await db.flush()

    event = UsageEvent(user_id=user_id, org_id=org_id, event_type="run_triggered", run_id=run.id)
    db.add(event)

    # Fire-and-forget — execution runs in background
    asyncio.create_task(_execute_run(run.id, org_id))

    return run


async def get_run_or_404(run_id: str, user_id: str, db: AsyncSession) -> DecisionRun:
    result = await db.execute(select(DecisionRun).where(DecisionRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    problem = await get_problem_or_404(run.problem_id, user_id, db)
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

async def _execute_run(run_id: str, org_id: str | None) -> None:
    """
    Runs outside the request context. Opens its own DB session.
    Executes the four-agent pipeline sequentially (mirroring the original LangGraph flow)
    and writes all outputs to the database.
    """
    from api.core.database import AsyncSessionLocal
    from api.core.config import settings

    async with AsyncSessionLocal() as db:
        try:
            run_result = await db.execute(select(DecisionRun).where(DecisionRun.id == run_id))
            run = run_result.scalar_one()
            run.status = RunStatus.running
            await db.commit()

            configs = await _get_active_configs(db, org_id)

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
                model, system_prompt, temperature, max_tokens = _cfg(agent_name)
                prompt = prompt_fn(run.problem_text, system_prompt)
                output, latency_ms = await _call_ollama(
                    settings.OLLAMA_HOST, model, prompt, temperature, max_tokens
                )
                state[agent_name] = output

                config_id = configs[agent_name].id if agent_name in configs else None
                ao = AgentOutput(
                    run_id=run.id,
                    agent_name=agent_name,
                    agent_config_id=config_id,
                    prompt_used=prompt,
                    raw_output=output,
                    latency_ms=latency_ms,
                )
                db.add(ao)

            model, system_prompt, temperature, max_tokens = _cfg("hades")
            hades_prompt = _build_hades_prompt(state, system_prompt)
            final_output, latency_ms = await _call_ollama(
                settings.OLLAMA_HOST, model, hades_prompt, temperature, max_tokens
            )

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

        except Exception as exc:
            await db.rollback()
            run_result = await db.execute(select(DecisionRun).where(DecisionRun.id == run_id))
            run = run_result.scalar_one_or_none()
            if run:
                run.status = RunStatus.failed
                await db.commit()


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_sales_prompt(problem: str, system_override: str) -> str:
    base = system_override or "You are a Sales Strategist. Give ONE clear recommendation for this problem."
    return f"{base}\n\nProblem: {problem}\n\nRecommendation:"

def _build_ops_prompt(problem: str, system_override: str) -> str:
    base = system_override or (
        "You are an Operations Strategist. You focus on execution, scalability, and bottlenecks. "
        "If it will fail in production, say it clearly. Give one realistic recommendation."
    )
    return f"{base}\n\nProblem: {problem}\n\nRecommendation:"

def _build_finance_prompt(problem: str, system_override: str) -> str:
    base = system_override or (
        "You are a Financial Strategist (CFA, CA). You protect cash flow, margins, and risk exposure. "
        "Give ONE recommendation."
    )
    return f"{base}\n\nProblem: {problem}\n\nRecommendation:"

def _build_hades_prompt(state: dict, system_override: str) -> str:
    base = system_override or (
        "You are Hades, the final decision maker. Synthesize the inputs from all three strategists "
        "and provide a concise final recommendation that balances all perspectives."
    )
    return (
        f"{base}\n\n"
        f"Sales:\n{state.get('sales', '')}\n\n"
        f"Operations:\n{state.get('operations', '')}\n\n"
        f"Finance:\n{state.get('finance', '')}\n\n"
        f"Problem:\n{state.get('problem', '')}\n\n"
        f"Final recommendation:"
    )
