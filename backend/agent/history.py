from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import numpy as np
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
    source_event_id: str | None = None,
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
            source=e.source,
            source_event_id=e.source_event_id,
            event_context=json.loads(e.event_context or "{}"),
        )
        for e in recent_rows
    ]

    # Query 2: similar events across same site in last N hours
    since_site = now - timedelta(hours=site_window_hours)
    site_stream_ids_q = await db.execute(
        select(Stream.id).where(Stream.site_id == site_id)
    )
    site_stream_ids = [r for r in site_stream_ids_q.scalars().all()]
    from backend.agent.memory import load_memory

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
                        source=e.source,
                        source_event_id=e.source_event_id,
                        event_context=json.loads(e.event_context or "{}"),
                    )
                )

    # Query 3: baseline stats for camera - use consistent time window
    # Use same time window for baseline as recent events for valid comparison
    baseline_window_hours = same_camera_window_seconds / 3600.0
    baseline_since = now - timedelta(hours=baseline_window_hours * 3)  # 3x window for baseline
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
        # events per hour
        camera_baseline[row.severity] = round(row.cnt / baseline_window_hours, 3)

    # Anomaly score: enhanced z-score with time-of-day and category context
    # Compare current window event rate to baseline rate (both in events/hour)
    current_rate = len(recent_events) / baseline_window_hours
    baseline_total = sum(camera_baseline.values())
    anomaly_score = 0.0
    
    if baseline_total > 0:
        # Get hourly baseline to apply time-of-day context (adaptive per camera)
        hourly_baseline = await _compute_hourly_baseline(
            db, stream_id, baseline_since
        )
        expected_rate = hourly_baseline.get(now.hour, baseline_total)
        
        # Determine if current hour is "quiet" based on learned schedule or heuristic
        is_quiet_hours = _is_quiet_hour(now.hour, hourly_baseline)
        
        # Get category distribution baseline for pattern rarity
        category_baseline = await _compute_category_baseline(
            db, stream_id, baseline_since
        )
        current_categories = {}
        for event in recent_events:
            for cat in event.categories:
                current_categories[cat] = current_categories.get(cat, 0) + 1
        
        # Compute z-score with time-of-day weighting
        deviation = current_rate - expected_rate
        std_estimate = max(
            np.sqrt(max(expected_rate, 1.0) * baseline_window_hours) / baseline_window_hours, 0.1
        )
        base_score = deviation / std_estimate
        
        # Apply quiet hours amplification (suspicious activity during expected-quiet times)
        if is_quiet_hours and deviation > 0:
            base_score *= 1.3  # 30% amplification during quiet hours
        
        # Apply category rarity boost (unusual event types)
        for cat, count in current_categories.items():
            baseline_cat_rate = category_baseline.get(cat, count)
            if count > baseline_cat_rate * 1.5:
                base_score += 0.2  # Boost for unusual category spike
        
        anomaly_score = round(min(max(base_score, -3.0), 3.0), 3)

    return HistoryContext(
        recent_events=recent_events,
        similar_events=similar_events[:20],
        camera_baseline=camera_baseline,
        site_baseline={"site_id": site_id, "stream_count": len(site_stream_ids)},
        anomaly_score=anomaly_score,
        memory_items=await load_memory(
            db,
            site_id=site_id,
            stream_id=stream_id,
            source_event_id=source_event_id,
        ),
    )


async def _compute_hourly_baseline(
    db: AsyncSession,
    stream_id: str,
    since: datetime,
) -> dict[int, float]:
    """Compute per-hour baseline for time-of-day context.
    
    Returns dict mapping hour (0-23) to average events/hour during that hour.
    """
    result = await db.execute(
        select(
            func.extract("hour", Event.timestamp).label("hour"),
            func.count(Event.id).label("cnt"),
        )
        .where(Event.stream_id == stream_id)
        .where(Event.timestamp >= since)
        .where(Event.verdict_action == "alert")
        .group_by(func.extract("hour", Event.timestamp))
    )
    rows = result.all()
    
    hourly_baseline: dict[int, float] = {}
    total_events = sum(r[1] for r in rows)
    
    for hour, count in rows:
        # Estimate events per hour at this time
        hourly_baseline[int(hour)] = count / 3.0 if total_events > 0 else 0.0
    
    return hourly_baseline


async def _compute_category_baseline(
    db: AsyncSession,
    stream_id: str,
    since: datetime,
) -> dict[str, float]:
    """Compute per-category baseline for pattern rarity detection.
    
    Returns dict mapping category (person, pet, package, etc.) 
    to average frequency in baseline period.
    """
    result = await db.execute(
        select(Event.categories)
        .where(Event.stream_id == stream_id)
        .where(Event.timestamp >= since)
        .where(Event.verdict_action == "alert")
    )
    rows = result.scalars().all()
    
    category_counts: dict[str, int] = {}
    total_events = len(rows)
    
    for cat_json in rows:
        try:
            categories = json.loads(cat_json)
            for cat in categories:
                category_counts[cat] = category_counts.get(cat, 0) + 1
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Normalize to frequency rate
    category_baseline: dict[str, float] = {}
    for cat, count in category_counts.items():
        category_baseline[cat] = count / max(total_events, 1)
    
    return category_baseline


def _is_quiet_hour(hour: int, hourly_baseline: dict[int, float]) -> bool:
    """Determine if an hour is quiet based on learned baseline (adaptive per camera).
    
    Quiet = bottom 20% of activity hours. Never assume global quiet hours.
    """
    if not hourly_baseline:
        # Fallback: if no baseline, assume nights are quiet (conservative)
        return hour < 6 or hour >= 20
    
    all_rates = list(hourly_baseline.values())
    if not all_rates:
        return hour < 6 or hour >= 20
    
    # Calculate 20th percentile (bottom quiet tier)
    sorted_rates = sorted(all_rates)
    percentile_20_idx = max(0, len(sorted_rates) // 5)
    threshold = sorted_rates[percentile_20_idx]
    
    current_rate = hourly_baseline.get(hour, 0)
    return current_rate <= threshold
