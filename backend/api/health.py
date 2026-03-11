from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.agent.reasoning.base import get_reasoning_runtime_status
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.policy import BLESSED_STACK, POLICY_VERSION, PROMPT_VERSION, RELEASE_LATENCY_BUDGET_MS
from backend.provider import active_reasoning_model, active_vision_model
from backend.runtime import memory_enabled

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "novin-security"}


async def build_readiness_report() -> dict:
    reasoning_status = get_reasoning_runtime_status()
    checks = {
        "db": False,
        "provider_api_key": bool(
            settings.together_api_key
            if settings.vision_provider == "together"
            else settings.siliconflow_api_key
            if settings.vision_provider == "siliconflow"
            else settings.groq_api_key
        ),
        "ingest_credential": bool(settings.ingest_api_key or settings.local_api_credential),
        "reasoning_live": reasoning_status["live"],
        "memory_enabled": memory_enabled(),
    }
    if settings.reasoning_provider == "together":
        checks["provider_api_key"] = checks["provider_api_key"] and bool(settings.together_api_key)
    elif settings.reasoning_provider == "cerebras":
        checks["provider_api_key"] = checks["provider_api_key"] and bool(settings.cerebras_api_key)
    elif settings.reasoning_provider == "siliconflow":
        checks["provider_api_key"] = checks["provider_api_key"] and bool(settings.siliconflow_api_key)

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception:
        checks["db"] = False

    ready = all(checks.values())
    return {
        "status": "ok" if ready else "degraded",
        "service": "novin-security",
        "checks": checks,
        "reasoning_status": reasoning_status,
        "vision_model": active_vision_model(),
        "vision_provider": settings.vision_provider,
        "reasoning_provider": settings.reasoning_provider,
        "memory_enabled": memory_enabled(),
        "reasoning_model": active_reasoning_model(),
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "blessed_stack": BLESSED_STACK,
        "latency_budget_ms": RELEASE_LATENCY_BUDGET_MS,
    }


@router.get("/health/ready")
async def health_ready():
    report = await build_readiness_report()
    status_code = 200 if report["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=report)
