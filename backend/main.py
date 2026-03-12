from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from backend.config import settings
from backend.database import init_db
from backend.hub import pipeline_manager, ws_manager
from backend.models.db import Stream
from backend.provider import active_reasoning_model, active_vision_model
from backend.runtime import (
    reset_benchmark_enabled,
    reset_memory_enabled,
    set_benchmark_enabled,
    set_memory_enabled,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initialising database...")
    await init_db()
    from backend.database import AsyncSessionLocal
    pipeline_manager.init(db_factory=AsyncSessionLocal)

    # Pre-flight: validate LLM provider API keys before accepting traffic
    _preflight_check_providers()

    from backend.agent.reasoning.base import warmup_cerebras_reasoning

    await warmup_cerebras_reasoning()

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Stream).where(Stream.active.is_(True)))
        active_streams = result.scalars().all()

        for stream in active_streams:
            try:
                await pipeline_manager.start(
                    stream_id=stream.id,
                    uri=stream.uri,
                    label=stream.label,
                    site_id=stream.site_id,
                    zone=stream.zone,
                )
            except Exception as exc:
                logger.error("Failed to resume stream %s: %s", stream.id, exc)
                stream.active = False

        await db.commit()
    logger.info(
        "Novin Home started. vision_provider=%s vision=%s reasoning_provider=%s reasoning_model=%s",
        settings.vision_provider,
        active_vision_model(),
        settings.reasoning_provider,
        active_reasoning_model(),
    )
    _log_startup_config()
    yield
    # Shutdown
    logger.info("Shutting down — stopping all pipelines...")
    await pipeline_manager.stop_all()


def _preflight_check_providers() -> None:
    """Validate required LLM provider API keys on startup. Raises on misconfiguration."""
    provider_key_map = {
        "vision": {
            "groq": settings.groq_api_key,
            "together": settings.together_api_key,
            "siliconflow": settings.siliconflow_api_key,
        },
        "reasoning": {
            "groq": settings.groq_api_key,
            "cerebras": settings.cerebras_api_key,
            "together": settings.together_api_key,
            "siliconflow": settings.siliconflow_api_key,
        },
    }
    errors = []
    vision_key = provider_key_map["vision"].get(settings.vision_provider)
    if not vision_key:
        errors.append(f"vision_provider={settings.vision_provider!r} requires an API key but none is set")
    reasoning_key = provider_key_map["reasoning"].get(settings.reasoning_provider)
    if not reasoning_key:
        errors.append(f"reasoning_provider={settings.reasoning_provider!r} requires an API key but none is set")
    if not (settings.ingest_api_key or settings.local_api_credential):
        errors.append("No ingest credential configured — set INGEST_API_KEY or LOCAL_API_CREDENTIAL")
    if errors:
        for e in errors:
            logger.critical("PREFLIGHT FAILURE: %s", e)
        raise RuntimeError("Startup aborted due to missing configuration:\n" + "\n".join(f"  • {e}" for e in errors))
    logger.info("Pre-flight check passed: vision=%s reasoning=%s credentials=ok", settings.vision_provider, settings.reasoning_provider)


def _log_startup_config() -> None:
    """Log a masked summary of the active configuration for operator visibility."""
    def _mask(v: str | None) -> str:
        if not v:
            return "(not set)"
        return v[:6] + "…" if len(v) > 8 else "(set)"

    webhook_urls = {}
    import os
    for k, v in os.environ.items():
        if k.startswith("WEBHOOK_URL_") and k != "WEBHOOK_URL":
            home_id = k[len("WEBHOOK_URL_"):]
            webhook_urls[home_id] = v[:30] + "…" if len(v) > 30 else v

    logger.info(
        "=== NOVIN HOME PILOT CONFIG ===\n"
        "  shadow_mode        : %s  ← %s\n"
        "  vision_provider    : %s  model=%s\n"
        "  reasoning_provider : %s  model=%s\n"
        "  webhook_url        : %s\n"
        "  webhook_secret     : %s\n"
        "  webhook_retries    : %d  timeout=%.1fs\n"
        "  slack              : %s\n"
        "  smtp               : %s\n"
        "  per_home_webhooks  : %s\n"
        "  db_url             : %s\n"
        "  ingest_credential  : %s\n"
        "  cors_origins       : %s\n"
        "================================",
        settings.shadow_mode,
        "SAFE — notifications suppressed" if settings.shadow_mode else "LIVE — notifications active",
        settings.vision_provider, active_vision_model(),
        settings.reasoning_provider, active_reasoning_model(),
        _mask(settings.webhook_url),
        "(set)" if settings.webhook_secret else "(not set — payloads unsigned)",
        settings.webhook_max_retries, settings.webhook_timeout_s,
        _mask(settings.slack_webhook_url),
        settings.smtp_host or "(not set)",
        webhook_urls or "(none)",
        settings.db_url.split("@")[-1] if "@" in settings.db_url else settings.db_url[:40],
        "(set)" if (settings.ingest_api_key or settings.local_api_credential) else "(NOT SET — BLOCKING)",
        settings.cors_origins,
    )


def create_app() -> FastAPI:
    reasoning_model = active_reasoning_model()
    vision_model = active_vision_model()
    app = FastAPI(
        title="Novin Home — Multi-Agent Vision System",
        version="1.0.0",
        description="Home security vision agent with multi-agent reasoning.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add Basic HTTP Auth middleware (executes before API key check)
    from backend.auth import BasicAuthMiddleware
    app.add_middleware(BasicAuthMiddleware)

    from backend.api.health import router as health_router
    from backend.api.health import build_readiness_report
    from backend.api.streams import router as streams_router
    from backend.api.events import router as events_router
    from backend.api.ingest import router as ingest_router
    from backend.api.novin.ingest import router as novin_ingest_router
    from backend.api.metrics import router as metrics_router
    from backend.api.webhooks import router as webhooks_router

    app.include_router(health_router)
    app.include_router(streams_router)
    app.include_router(events_router)
    app.include_router(ingest_router)
    app.include_router(novin_ingest_router)
    app.include_router(metrics_router)
    app.include_router(webhooks_router)

    @app.middleware("http")
    async def require_api_credential(request: Request, call_next):
        memory_token = None
        benchmark_token = None
        memory_header = request.headers.get("x-novin-memory")
        if memory_header is not None:
            value = memory_header.strip().lower()
            if value in {"on", "true", "1"}:
                memory_token = set_memory_enabled(True)
            elif value in {"off", "false", "0"}:
                memory_token = set_memory_enabled(False)
        benchmark_header = request.headers.get("x-novin-benchmark")
        if benchmark_header is not None:
            value = benchmark_header.strip().lower()
            benchmark_token = set_benchmark_enabled(value in {"on", "true", "1"})
        # Ingest paths require credentials (non-negotiable)
        try:
            ingest_paths = ("/api/novin/ingest", "/api/ingest")
            is_ingest = any(request.url.path.startswith(p) for p in ingest_paths)
            if is_ingest:
                valid_keys = [
                    k for k in (settings.ingest_api_key, settings.local_api_credential) if k
                ]
                if not valid_keys:
                    return JSONResponse(
                        status_code=503,
                        content={"detail": "Ingest API key not configured. Set INGEST_API_KEY or LOCAL_API_CREDENTIAL."},
                    )
                provided = request.headers.get("x-api-key") or request.headers.get("X-Api-Key")
                if provided not in valid_keys:
                    return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
            elif settings.local_api_credential and request.url.path.startswith("/api"):
                provided = request.headers.get("x-api-key") or request.headers.get("X-Api-Key")
                if provided != settings.local_api_credential:
                    return JSONResponse(status_code=401, content={"detail": "Invalid API credential"})
            return await call_next(request)
        finally:
            if memory_token is not None:
                reset_memory_enabled(memory_token)
            if benchmark_token is not None:
                reset_benchmark_enabled(benchmark_token)

    @app.get("/api/status")
    async def status():
        from backend.api.novin.ingest import async_ingest_failure_count
        from backend.metrics import get_metrics
        
        readiness = await build_readiness_report()
        metrics = get_metrics().snapshot()
        
        return {
            "active_streams": pipeline_manager.active_count,
            "active_stream_ids": pipeline_manager.active_stream_ids,
            "ws_connections": ws_manager.connection_count,
            "vision_model": vision_model,
            "vision_provider": settings.vision_provider,
            "reasoning_provider": settings.reasoning_provider,
            "reasoning_model": reasoning_model,
            "memory_enabled": settings.enable_agent_memory,
            "shadow_mode": settings.shadow_mode,
            "reasoning_live": readiness["checks"]["reasoning_live"],
            "reasoning_degraded": not readiness["checks"]["reasoning_live"],
            "async_ingest_failures": async_ingest_failure_count(),
            "frame_drops_by_stream": get_metrics().frame_drop_counts(),
            "metrics_summary": {
                "pipeline_p95_ms": metrics["latency"]["pipeline_p95_ms"],
                "requests_1h": metrics["throughput"]["requests_1h"],
                "alert_rate_1h": metrics["actions"]["alert_rate_1h"],
                "errors_1h": metrics["errors"]["total_1h"],
            },
            "readiness": readiness,
        }

    return app


app = create_app()
