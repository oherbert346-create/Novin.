from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models.db import AgentTrace, Event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def list_events(
    stream_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    since: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Event).order_by(Event.timestamp.desc()).limit(limit)
    if stream_id:
        q = q.where(Event.stream_id == stream_id)
    if severity:
        q = q.where(Event.severity == severity)
    if action:
        q = q.where(Event.verdict_action == action)
    if since:
        q = q.where(Event.timestamp >= since)

    result = await db.execute(q)
    events = result.scalars().all()

    event_ids = [e.id for e in events]
    traces_by_event: dict[str, list[AgentTrace]] = {event_id: [] for event_id in event_ids}
    if event_ids:
        traces_q = await db.execute(
            select(AgentTrace).where(AgentTrace.event_id.in_(event_ids))
        )
        for trace in traces_q.scalars().all():
            traces_by_event.setdefault(trace.event_id, []).append(trace)

    out = []
    for e in events:
        traces = traces_by_event.get(e.id, [])
        out.append(
            {
                "id": e.id,
                "stream_id": e.stream_id,
                "timestamp": e.timestamp.isoformat(),
                "severity": e.severity,
                "categories": json.loads(e.categories),
                "description": e.description,
                "bbox": json.loads(e.bbox),
                "b64_thumbnail": e.b64_thumbnail,
                "verdict_action": e.verdict_action,
                "final_confidence": e.final_confidence,
                "summary": e.summary,
                "narrative_summary": e.narrative_summary,
                "alert_reason": e.alert_reason,
                "suppress_reason": e.suppress_reason,
                "agent_traces": [
                    {
                        "agent_id": t.agent_id,
                        "role": t.role,
                        "verdict": t.verdict,
                        "confidence": t.confidence,
                        "rationale": t.rationale,
                        "chain_notes": json.loads(t.chain_notes),
                    }
                    for t in traces
                ],
            }
        )
    return out


@router.get("/events/{event_id}")
async def get_event(event_id: str, db: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException

    result = await db.execute(select(Event).where(Event.id == event_id))
    e = result.scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Event not found")

    traces_q = await db.execute(
        select(AgentTrace).where(AgentTrace.event_id == e.id)
    )
    traces = traces_q.scalars().all()

    return {
        "id": e.id,
        "stream_id": e.stream_id,
        "timestamp": e.timestamp.isoformat(),
        "severity": e.severity,
        "categories": json.loads(e.categories),
        "description": e.description,
        "bbox": json.loads(e.bbox),
        "b64_thumbnail": e.b64_thumbnail,
        "verdict_action": e.verdict_action,
        "final_confidence": e.final_confidence,
        "summary": e.summary,
        "narrative_summary": e.narrative_summary,
        "alert_reason": e.alert_reason,
        "suppress_reason": e.suppress_reason,
        "agent_traces": [
            {
                "agent_id": t.agent_id,
                "role": t.role,
                "verdict": t.verdict,
                "confidence": t.confidence,
                "rationale": t.rationale,
                "chain_notes": json.loads(t.chain_notes),
            }
            for t in traces
        ],
    }


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    credential = settings.local_api_credential
    if credential:
        provided = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")
        if provided != credential:
            await websocket.close(code=1008)
            return

    from backend.hub import ws_manager
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
