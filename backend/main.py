from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from backend.config import settings
from backend.database import init_db
from backend.hub import pipeline_manager, ws_manager
from backend.models.db import Stream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Novin Security — Multi-Agent Vision System",
        version="1.0.0",
        description="Enterprise physical security vision agent with multi-agent reasoning.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from backend.api.health import router as health_router
    from backend.api.streams import router as streams_router
    from backend.api.events import router as events_router
    from backend.api.ingest import router as ingest_router

    app.include_router(health_router)
    app.include_router(streams_router)
    app.include_router(events_router)
    app.include_router(ingest_router)

    @app.middleware("http")
    async def require_local_api_credential(request: Request, call_next):
        credential = settings.local_api_credential
        if credential and request.url.path.startswith("/api"):
            provided = request.headers.get("x-api-key")
            if provided != credential:
                return JSONResponse(status_code=401, content={"detail": "Invalid API credential"})
        return await call_next(request)

    @app.on_event("startup")
    async def startup():
        logger.info("Initialising database...")
        await init_db()
        from backend.database import AsyncSessionLocal
        pipeline_manager.init(db_factory=AsyncSessionLocal)

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
        logger.info("Novin Security started. Vision model: %s", settings.groq_vision_model)

    @app.on_event("shutdown")
    async def shutdown():
        logger.info("Shutting down — stopping all pipelines...")
        await pipeline_manager.stop_all()

    @app.get("/api/status")
    async def status():
        reasoning_model = (
            settings.cerebras_reasoning_model
            if settings.reasoning_provider == "cerebras"
            else settings.groq_reasoning_model
        )
        return {
            "active_streams": pipeline_manager.active_count,
            "active_stream_ids": pipeline_manager.active_stream_ids,
            "ws_connections": ws_manager.connection_count,
            "vision_model": settings.groq_vision_model,
            "reasoning_provider": settings.reasoning_provider,
            "reasoning_model": reasoning_model,
        }

    return app


app = create_app()
