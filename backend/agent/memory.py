from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db import AgentMemory
from backend.models.schemas import MemoryItem, Verdict
from backend.runtime import memory_enabled


def _upsert_targets(verdict: Verdict) -> list[tuple[str, str, str]]:
    targets = [
        ("site", verdict.site_id, f"site:{verdict.stream_id}:{verdict.routing.risk_level}"),
        ("stream", verdict.stream_id, f"stream:{verdict.routing.risk_level}"),
    ]
    source_event_id = verdict.event_context.source_event_id if verdict.event_context else None
    if source_event_id:
        targets.append(("source_event", source_event_id, "source_event:thread"))
    return targets


def _summary_for_verdict(verdict: Verdict) -> str:
    return (
        f"{verdict.routing.risk_level} risk on {verdict.stream_id}: "
        f"{verdict.summary.headline[:140]}"
    )


def _details_for_verdict(verdict: Verdict) -> dict:
    metadata = verdict.event_context.metadata if verdict.event_context else {}
    preferences = metadata.get("preferences", {}) if isinstance(metadata, dict) else {}
    preference_tags = sorted(str(key) for key in preferences.keys())
    categories = list(verdict.routing.categories[:3])
    return {
        "event_type": categories[0] if categories else "clear",
        "categories": categories,
        "last_action": verdict.routing.action,
        "risk_level": verdict.routing.risk_level,
        "reasoning_degraded": any(
            output.rationale.startswith("Agent fallback:") for output in verdict.audit.agent_outputs
        ),
        "source": verdict.event_context.source if verdict.event_context else None,
        "source_event_id": verdict.event_context.source_event_id if verdict.event_context else None,
        "zone": verdict.event_context.zone if verdict.event_context else None,
        "preference_tags": preference_tags,
    }


async def update_memory(db: AsyncSession, verdict: Verdict) -> None:
    if not memory_enabled():
        return
    summary = _summary_for_verdict(verdict)
    details = _details_for_verdict(verdict)
    for scope_type, scope_id, memory_key in _upsert_targets(verdict):
        result = await db.execute(
            select(AgentMemory).where(
                AgentMemory.scope_type == scope_type,
                AgentMemory.scope_id == scope_id,
                AgentMemory.memory_key == memory_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = AgentMemory(
                scope_type=scope_type,
                scope_id=scope_id,
                memory_key=memory_key,
                summary=summary,
                details=json.dumps(details),
                last_event_id=verdict.event_id,
                hit_count=1,
            )
            db.add(row)
        else:
            row.summary = summary
            row.details = json.dumps(details)
            row.last_event_id = verdict.event_id
            row.hit_count += 1


async def load_memory(
    db: AsyncSession,
    site_id: str,
    stream_id: str,
    source_event_id: str | None = None,
) -> list[MemoryItem]:
    if not memory_enabled():
        return []
    clauses = [
        ((AgentMemory.scope_type == "site") & (AgentMemory.scope_id == site_id)),
        ((AgentMemory.scope_type == "stream") & (AgentMemory.scope_id == stream_id)),
    ]
    if source_event_id:
        clauses.append(
            ((AgentMemory.scope_type == "source_event") & (AgentMemory.scope_id == source_event_id))
        )
    query_clause = clauses[0] | clauses[1]
    if len(clauses) == 3:
        query_clause = query_clause | clauses[2]
    result = await db.execute(
        select(AgentMemory)
        .where(query_clause)
        .order_by(AgentMemory.updated_at.desc())
        .limit(10)
    )
    rows = result.scalars().all()
    now = datetime.now(timezone.utc)
    return [
        MemoryItem(
            scope_type=row.scope_type,
            scope_id=row.scope_id,
            memory_key=row.memory_key,
            summary=row.summary,
            details={
                **json.loads(row.details or "{}"),
                "last_seen_minutes": max(
                    0,
                    int((now - row.updated_at.replace(tzinfo=timezone.utc)).total_seconds() // 60),
                )
                if row.updated_at
                else None,
            },
            last_event_id=row.last_event_id,
            hit_count=row.hit_count,
        )
        for row in rows
    ]
