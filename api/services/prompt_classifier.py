"""
Two-stage prompt safety classifier.

Stage 1 — keyword scan: instant regex against hardcoded patterns. Hard hits
           block immediately, no LLM call.
Stage 2 — LLM classifier: separate Ollama call scoring harm 1-5.
           Only reached when Stage 1 passes. Severity >= 3 blocks.

On block: MonitoringFlag row written + email stub called.
On pass:  returns ClassifierResult with blocked=False — caller proceeds.
"""
import json
import re
import httpx
from dataclasses import dataclass
from typing import Optional


# ── Keyword patterns (Stage 1) ────────────────────────────────────────────────
_HARD_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(how to (make|build|synthesize|create).{0,30}(bomb|explosive|weapon|poison|nerve agent|bioweapon))\b", re.I), "weapons_synthesis"),
    (re.compile(r"\b(child.{0,10}(sex|porn|abuse|exploit|nude|naked))\b", re.I), "csam"),
    (re.compile(r"\b(how to (kill|murder|assassinate).{0,20}(person|people|someone|myself|yourself))\b", re.I), "violence"),
    (re.compile(r"\b(suicide.{0,20}(method|how|way|step|instruction))\b", re.I), "self_harm"),
    (re.compile(r"\b(hack.{0,20}(password|account|bank|government|military))\b", re.I), "cybercrime"),
    (re.compile(r"\b(synthesize|produce|manufacture).{0,30}(fentanyl|meth|heroin|cocaine|drug)\b", re.I), "drug_synthesis"),
    (re.compile(r"\b(terrorist|terrorism|jihad).{0,30}(attack|plan|target|bomb)\b", re.I), "terrorism"),
]


@dataclass
class ClassifierResult:
    blocked: bool
    stage: str                      # "keyword" | "llm" | "pass"
    pattern_matched: Optional[str]
    llm_reason: Optional[str]
    llm_severity: Optional[int]
    excerpt: str


def _keyword_scan(prompt: str) -> tuple[bool, Optional[str]]:
    for pattern, label in _HARD_PATTERNS:
        if pattern.search(prompt):
            return True, label
    return False, None


_CLASSIFIER_SYSTEM = """You are a content safety classifier. Evaluate whether a prompt submitted to a business decision-support AI is harmful, unethical, or dangerous.

Rate severity 1-5:
1 = Safe, normal business question
2 = Slightly sensitive but acceptable
3 = Borderline — could facilitate harm
4 = Clearly harmful or unethical
5 = Severely harmful or illegal

Respond ONLY with valid JSON:
{"safe": true/false, "severity": 1-5, "reason": "one sentence"}

Normal business decisions (sales, finance, operations, hiring, pricing) are always safe (1-2)."""


async def _llm_classify(prompt: str, ollama_host: str, model: str) -> tuple[bool, Optional[str], Optional[int]]:
    """Fails open — classifier outage must not block all users."""
    try:
        full_prompt = f"{_CLASSIFIER_SYSTEM}\n\nPrompt to evaluate:\n{prompt[:2000]}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ollama_host}/api/generate",
                json={"model": model, "prompt": full_prompt, "stream": False,
                      "options": {"temperature": 0.1, "num_predict": 150}},
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()

        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return False, None, None

        data = json.loads(match.group())
        severity = int(data.get("severity", 1))
        return severity >= 3, data.get("reason", ""), severity

    except Exception as exc:
        print(f"[classifier] LLM error (fail open): {exc}")
        return False, None, None


async def classify_prompt(
    prompt: str,
    user_id: Optional[str],
    org_id: Optional[str],
    run_id: Optional[str],
    request_id: Optional[str],
) -> ClassifierResult:
    from api.core.config import settings

    excerpt = prompt[:500]

    # Stage 1
    matched, label = _keyword_scan(prompt)
    if matched:
        result = ClassifierResult(blocked=True, stage="keyword",
                                  pattern_matched=label, llm_reason=None,
                                  llm_severity=5, excerpt=excerpt)
        await _write_flag(result, user_id, org_id, run_id, request_id)
        return result

    # Stage 2
    blocked, reason, severity = await _llm_classify(prompt, settings.OLLAMA_HOST, settings.OLLAMA_MODEL)
    if blocked:
        result = ClassifierResult(blocked=True, stage="llm",
                                  pattern_matched=None, llm_reason=reason,
                                  llm_severity=severity, excerpt=excerpt)
        await _write_flag(result, user_id, org_id, run_id, request_id)
        return result

    return ClassifierResult(blocked=False, stage="pass", pattern_matched=None,
                            llm_reason=reason, llm_severity=severity, excerpt=excerpt)


async def _write_flag(result: ClassifierResult, user_id, org_id, run_id, request_id) -> None:
    try:
        from api.core.database import AsyncSessionLocal
        from api.models.orm import MonitoringFlag, FlagType, FlagSeverity

        sev_map = {5: FlagSeverity.critical, 4: FlagSeverity.high, 3: FlagSeverity.medium}
        severity = sev_map.get(result.llm_severity or 5, FlagSeverity.critical)

        async with AsyncSessionLocal() as db:
            db.add(MonitoringFlag(
                flag_type=FlagType.unethical_prompt, severity=severity,
                user_id=user_id, org_id=org_id, run_id=run_id,
                request_id=request_id, prompt_excerpt=result.excerpt,
                classifier_reason=result.llm_reason,
                classifier_severity=result.llm_severity,
                meta={"stage": result.stage, "pattern_matched": result.pattern_matched},
            ))
            await db.commit()

        from api.services.email_service import send_flag_alert
        await send_flag_alert(
            flag_type="unethical_prompt", severity=severity.value,
            user_id=user_id, org_id=org_id,
            detail=result.llm_reason or f"Keyword: {result.pattern_matched}",
        )
    except Exception as exc:
        print(f"[classifier] flag write error (non-fatal): {exc}")