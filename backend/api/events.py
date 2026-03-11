from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models.db import AgentTrace, Event, HomeThresholdConfig, Stream

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["events"])


def _event_payload(e: Event, traces: list[AgentTrace]) -> dict:
    event_context = json.loads(e.event_context or "{}")
    metadata = event_context.get("metadata", {}) if isinstance(event_context, dict) else {}
    routing = metadata.get("routing", {}) if isinstance(metadata, dict) else {}
    case = metadata.get("case", {}) if isinstance(metadata, dict) else {}
    if not isinstance(case, dict):
        case = {}
    consumer_summary = case.get("consumer_summary", {})
    operator_summary = case.get("operator_summary", {})
    evidence_digest = case.get("evidence_digest", [])
    return {
        "id": e.id,
        "event_id": e.id,
        "stream_id": e.stream_id,
        "site_id": event_context.get("home_id", "home") if isinstance(event_context, dict) else "home",
        "timestamp": e.timestamp.isoformat(),
        "risk_level": routing.get("risk_level", e.severity),
        "action": e.verdict_action,
        "severity": e.severity,
        "visibility_policy": routing.get("visibility_policy", "hidden"),
        "notification_policy": routing.get("notification_policy", "none"),
        "storage_policy": routing.get("storage_policy", "diagnostic"),
        "categories": json.loads(e.categories),
        "description": e.description,
        "bbox": json.loads(e.bbox),
        "b64_thumbnail": e.b64_thumbnail,
        "summary": e.summary,
        "narrative_summary": e.narrative_summary,
        "decision_reason": routing.get("decision_reason") or e.alert_reason or e.suppress_reason,
        "alert_reason": e.alert_reason,
        "suppress_reason": e.suppress_reason,
        "reasoning_degraded": any(t.rationale.startswith("Agent fallback:") for t in traces),
        "event_context": event_context,
        "case": case,
        "case_id": case.get("case_id", e.id),
        "case_status": case.get("case_status", "routine"),
        "ambiguity_state": case.get("ambiguity_state", "resolved"),
        "confidence_band": case.get("confidence_band", "low"),
        "consumer_summary": consumer_summary,
        "operator_summary": operator_summary,
        "evidence_digest": evidence_digest if isinstance(evidence_digest, list) else [],
        "recommended_next_action": case.get("recommended_next_action", ""),
        "recommended_delivery_targets": case.get("recommended_delivery_targets", []),
        "agent_outputs": [
            {
                "agent_id": t.agent_id,
                "role": t.role,
                "verdict": t.verdict,
                "rationale": t.rationale,
                "chain_notes": json.loads(t.chain_notes),
            }
            for t in traces
        ],
        # User feedback and tagging
        "user_tag": e.user_tag,
        "user_feedback": e.user_feedback,
        "user_feedback_timestamp": e.user_feedback_timestamp.isoformat() if e.user_feedback_timestamp else None,
    }


async def require_api_key(x_api_key: str | None = Header(None)) -> str:
    """Require API key for protected endpoints."""
    if not settings.local_api_credential:
        # No credential configured - allow access (dev mode)
        return "dev"
    
    if x_api_key != settings.local_api_credential:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key


@router.get("/events")
async def list_events(
    stream_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    risk_level: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    since: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_api_key),
):
    q = select(Event).order_by(Event.timestamp.desc()).limit(limit)
    if stream_id:
        q = q.where(Event.stream_id == stream_id)
    if severity or risk_level:
        q = q.where(Event.severity == (risk_level or severity))
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
        out.append(_event_payload(e, traces))
    return out


@router.get("/events/{event_id}")
async def get_event(
    event_id: str, 
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_api_key),
):
    from fastapi import HTTPException

    result = await db.execute(select(Event).where(Event.id == event_id))
    e = result.scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Event not found")

    traces_q = await db.execute(
        select(AgentTrace).where(AgentTrace.event_id == e.id)
    )
    traces = traces_q.scalars().all()

    return _event_payload(e, traces)


@router.post("/events/{event_id}/tag")
async def tag_event(
    event_id: str,
    tag: str,
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_api_key),
):
    """Tag an event with identity information (resident, guest, vendor, unknown)."""
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if tag not in ("resident", "guest", "vendor", "unknown"):
        raise HTTPException(
            status_code=400, 
            detail="tag must be one of: resident, guest, vendor, unknown"
        )
    
    event.user_tag = tag
    await db.commit()
    
    # Update memory with this tagging information
    from backend.agent.memory import update_memory
    from backend.models.schemas import Verdict, EventContext, MachineRouting, AuditTrail, LiabilityDigest, OperatorSummary
    
    # Create a synthetic verdict for memory update
    verdict = Verdict(
        frame_id=event.id,
        event_id=event.id,
        stream_id=event.stream_id,
        site_id="home",  # Will be extracted from context
        timestamp=event.timestamp,
        routing=MachineRouting(
            is_threat=event.verdict_action == "alert",
            action=event.verdict_action,
            risk_level=event.severity,
            severity=event.severity,
            categories=json.loads(event.categories),
        ),
        summary=OperatorSummary(headline=event.summary, narrative=event.narrative_summary),
        audit=AuditTrail(
            liability_digest=LiabilityDigest(
                decision_reasoning=f"User tagged as {tag}",
                confidence_score=event.final_confidence,
            ),
            agent_outputs=[],
        ),
        description=event.description,
        bbox=[],
        b64_thumbnail=event.b64_thumbnail,
        event_context=EventContext(
            source=event.source,
            source_event_id=event.source_event_id,
            zone=event.zone,
        ),
    )
    await update_memory(db, verdict)
    
    logger.info("Tagged event %s as %s", event_id, tag)
    return {"event_id": event_id, "tag": tag, "status": "success"}


@router.post("/events/{event_id}/feedback")
async def provide_feedback(
    event_id: str,
    feedback: str,
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_api_key),
):
    """Provide feedback on verdict accuracy (false_positive, false_negative)."""
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if feedback not in ("false_positive", "false_negative", "correct"):
        raise HTTPException(
            status_code=400,
            detail="feedback must be one of: false_positive, false_negative, correct"
        )
    
    event.user_feedback = feedback
    event.user_feedback_timestamp = datetime.utcnow()
    await db.commit()
    
    # Update threshold config counters
    stream_result = await db.execute(select(Stream).where(Stream.id == event.stream_id))
    stream = stream_result.scalar_one_or_none()
    
    if stream:
        site_id = stream.site_id or "home"
        config_result = await db.execute(
            select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == site_id)
        )
        config = config_result.scalar_one_or_none()
        
        if config:
            # Increment feedback counters
            config.total_alerts_30d = (config.total_alerts_30d or 0) + 1
            if feedback == "false_positive":
                config.fp_count_30d = (config.fp_count_30d or 0) + 1
            elif feedback == "false_negative":
                config.fn_count_30d = (config.fn_count_30d or 0) + 1
            # "correct" feedback doesn't increment FP or FN counters
            
            await db.commit()
            logger.info(
                "Updated thresholds for site %s: fp=%d fn=%d total=%d",
                site_id,
                config.fp_count_30d,
                config.fn_count_30d,
                config.total_alerts_30d,
            )
    
    # Update memory with feedback
    from backend.agent.memory import update_memory
    from backend.models.schemas import Verdict, EventContext, MachineRouting, AuditTrail, LiabilityDigest, OperatorSummary
    
    verdict = Verdict(
        frame_id=event.id,
        event_id=event.id,
        stream_id=event.stream_id,
        site_id="home",
        timestamp=event.timestamp,
        routing=MachineRouting(
            is_threat=event.verdict_action == "alert",
            action=event.verdict_action,
            risk_level=event.severity,
            severity=event.severity,
            categories=json.loads(event.categories),
        ),
        summary=OperatorSummary(headline=event.summary, narrative=event.narrative_summary),
        audit=AuditTrail(
            liability_digest=LiabilityDigest(
                decision_reasoning=f"User feedback: {feedback}",
                confidence_score=event.final_confidence,
            ),
            agent_outputs=[],
        ),
        description=event.description,
        bbox=[],
        b64_thumbnail=event.b64_thumbnail,
        event_context=EventContext(
            source=event.source,
            source_event_id=event.source_event_id,
            zone=event.zone,
        ),
    )
    await update_memory(db, verdict)
    
    logger.info("Feedback recorded for event %s: %s", event_id, feedback)
    return {"event_id": event_id, "feedback": feedback, "timestamp": datetime.utcnow().isoformat(), "status": "success"}


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
