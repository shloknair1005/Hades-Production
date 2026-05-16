"""
chat_service.py
Handles 1:1 follow-up conversations between a user and a specific agent
after the initial run is complete.
"""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from api.models.orm import AgentChatMessage, AgentOutput, DecisionRun
from api.services.run_service import get_run_or_404, _call_ollama, _get_active_configs
from api.core.config import settings

# Agent personas used when no custom system_prompt is configured
_PERSONAS: dict[str, str] = {
    "sales": (
        "You are a Sales Strategist. You previously gave a recommendation on a business problem. "
        "The user wants to dig deeper with you specifically. Stay in character as the Sales Strategist. "
        "Be direct, actionable, and commercially focused."
    ),
    "operations": (
        "You are an Operations Strategist. You previously gave a recommendation on a business problem. "
        "The user wants to discuss it further with you. Stay in character as the Operations Strategist. "
        "Focus on execution, process, scalability, and risk."
    ),
    "finance": (
        "You are a Financial Strategist. You previously gave a recommendation on a business problem. "
        "The user wants to explore it further with you. Stay in character as the Financial Strategist. "
        "Focus on numbers, cash flow, ROI, and financial risk."
    ),
    "hades": (
        "You are Hades, the final arbiter. You synthesised the views of Sales, Operations, and Finance "
        "into a final verdict. The user wants to discuss your verdict further. "
        "Be balanced, decisive, and reference the full picture."
    ),
}

VALID_AGENTS = {"sales", "operations", "finance", "hades"}


async def get_chat_history(
    run_id: str,
    agent_name: str,
    user_id: str,
    db: AsyncSession,
) -> list[AgentChatMessage]:
    """Return all chat messages for a specific agent in a run (ordered oldest-first)."""
    if agent_name not in VALID_AGENTS:
        raise HTTPException(status_code=400, detail=f"Unknown agent '{agent_name}'")

    await get_run_or_404(run_id, user_id, db)

    result = await db.execute(
        select(AgentChatMessage)
        .where(
            AgentChatMessage.run_id == run_id,
            AgentChatMessage.agent_name == agent_name,
            AgentChatMessage.user_id == user_id,
        )
        .order_by(AgentChatMessage.created_at)
    )
    return result.scalars().all()


async def send_chat_message(
    run_id: str,
    agent_name: str,
    user_message: str,
    user_id: str,
    org_id: str | None,
    db: AsyncSession,
) -> AgentChatMessage:
    """
    Persist the user's message, build the full prompt with context + history,
    call Ollama, persist the assistant reply, and return the assistant message.
    """
    if agent_name not in VALID_AGENTS:
        raise HTTPException(status_code=400, detail=f"Unknown agent '{agent_name}'")

    run = await get_run_or_404(run_id, user_id, db)

    if run.status.value != "done":
        raise HTTPException(
            status_code=409,
            detail="Chat is only available after the run has completed.",
        )

    # ── Fetch the agent's original output for context ─────────────────────────
    agent_output_result = await db.execute(
        select(AgentOutput).where(
            AgentOutput.run_id == run_id,
            AgentOutput.agent_name == agent_name,
        )
    )
    agent_output = agent_output_result.scalar_one_or_none()
    original_answer = agent_output.raw_output if agent_output else "(no original output found)"

    # ── Fetch existing history ────────────────────────────────────────────────
    history = await get_chat_history(run_id, agent_name, user_id, db)

    # ── Persist user message ──────────────────────────────────────────────────
    user_msg = AgentChatMessage(
        run_id=run_id,
        user_id=user_id,
        agent_name=agent_name,
        role="user",
        content=user_message,
    )
    db.add(user_msg)
    await db.flush()

    # ── Build prompt ──────────────────────────────────────────────────────────
    configs = await _get_active_configs(db, org_id)
    cfg = configs.get(agent_name)
    system_prompt = (cfg.system_prompt if cfg else None) or _PERSONAS.get(agent_name, "")
    model = cfg.model if cfg else settings.OLLAMA_MODEL
    temperature = cfg.temperature if cfg else 0.7
    max_tokens = cfg.max_tokens if cfg else 500

    # Build labelled conversation turns
    conversation_turns = ""
    for msg in history:
        label = "User" if msg.role == "user" else agent_name.capitalize()
        conversation_turns += f"{label}: {msg.content}\n\n"
    conversation_turns += f"User: {user_message}\n\n"

    chat_format_rules = (
        "REPLY FORMAT: Be concise and direct (under 120 words). "
        "If listing steps or options, use numbered points (N. Title: sentence). "
        "If answering a direct question, reply in 2-3 plain sentences. "
        "No preamble, no markdown, no asterisks."
    )

    prompt = (
        f"{system_prompt}\n\n"
        f"{chat_format_rules}\n\n"
        f"=== ORIGINAL PROBLEM ===\n{run.problem_text}\n\n"
        f"=== YOUR ORIGINAL RECOMMENDATION ===\n{original_answer}\n\n"
        f"=== CONVERSATION ===\n"
        f"{conversation_turns}"
        f"{agent_name.capitalize()}:"
    )

    # ── Call Ollama ───────────────────────────────────────────────────────────
    reply_text, _latency = await _call_ollama(
        settings.OLLAMA_HOST, model, prompt, temperature, max_tokens
    )

    # ── Persist assistant reply ───────────────────────────────────────────────
    assistant_msg = AgentChatMessage(
        run_id=run_id,
        user_id=user_id,
        agent_name=agent_name,
        role="assistant",
        content=reply_text,
    )
    db.add(assistant_msg)
    await db.commit()

    return assistant_msg
