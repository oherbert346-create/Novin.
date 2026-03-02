from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db import Event, Stream
from backend.models.schemas import HistoryContext, RecentEvent

logger = logging.getLogger(__name__)


async def query_history(
    db: AsyncSession,
    stream_id: str,
    site_id: str,
    event_types: list[str],
    same_camera_window_seconds: int = 300,
    site_window_hours: int = 24,
) -> HistoryContext:
    now = datetime.utcnow()

    # Query 1: recent events on same camera
    since_camera = now - timedelta(seconds=same_camera_window_seconds)
    q1 = await db.execute(
        select(Event)
        .where(Event.stream_id == stream_id)
        .where(Event.timestamp >= since_camera)
        .where(Event.verdict_action == "alert")
        .order_by(Event.timestamp.desc())
        .limit(20)
    )
    recent_rows = q1.scalars().all()
    recent_events = [
        RecentEvent(
            event_id=e.id,
            stream_id=e.stream_id,
            timestamp=e.timestamp,
            severity=e.severity,
            categories=json.loads(e.categories),
            description=e.description,
        )
        for e in recent_rows
    ]

    # Query 2: similar events across same site in last N hours
    since_site = now - timedelta(hours=site_window_hours)
    site_stream_ids_q = await db.execute(
        select(Stream.id).where(Stream.site_id == site_id)
    )
    site_stream_ids = [r for r in site_stream_ids_q.scalars().all()]

    similar_events: list[RecentEvent] = []
    if site_stream_ids and event_types:
        q2 = await db.execute(
            select(Event)
            .where(Event.stream_id.in_(site_stream_ids))
            .where(Event.timestamp >= since_site)
            .where(Event.verdict_action == "alert")
            .order_by(Event.timestamp.desc())
            .limit(50)
        )
        site_rows = q2.scalars().all()
        for e in site_rows:
            cats = json.loads(e.categories)
            if any(t in cats for t in event_types):
                similar_events.append(
                    RecentEvent(
                        event_id=e.id,
                        stream_id=e.stream_id,
                        timestamp=e.timestamp,
                        severity=e.severity,
                        categories=cats,
                        description=e.description,
                    )
                )

    # Query 3: baseline stats for camera (avg events/hour by severity)
    baseline_since = now - timedelta(hours=72)
    q3 = await db.execute(
        select(Event.severity, func.count(Event.id).label("cnt"))
        .where(Event.stream_id == stream_id)
        .where(Event.timestamp >= baseline_since)
        .where(Event.verdict_action == "alert")
        .group_by(Event.severity)
    )
    baseline_rows = q3.all()
    camera_baseline: dict[str, float] = {}
    for row in baseline_rows:
        camera_baseline[row.severity] = round(row.cnt / 72.0, 3)

    # Anomaly score: z-score approximation
    # Compare current window event count to baseline
    current_rate = len(recent_events) / (same_camera_window_seconds / 3600.0)
    baseline_total = sum(camera_baseline.values())
    anomaly_score = 0.0
    if baseline_total > 0:
        deviation = current_rate - baseline_total
        anomaly_score = round(min(max(deviation / max(baseline_total, 0.1), -3.0), 3.0), 3)

    return HistoryContext(
        recent_events=recent_events,
        similar_events=similar_events[:20],
        camera_baseline=camera_baseline,
        site_baseline={"site_id": site_id, "stream_count": len(site_stream_ids)},
        anomaly_score=anomaly_score,
    )
