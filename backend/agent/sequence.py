"""Temporal correlation - detects event sequences for behavior patterns."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db import Event

logger = logging.getLogger(__name__)

PERIMETER_ZONES = {"front_door", "backyard", "driveway", "porch", "garage"}
INTERIOR_ZONES = {"living_room", "kitchen", "bedroom", "bathroom", "hallway", "office"}


@dataclass
class SequenceEvent:
    event_id: str
    stream_id: str
    zone: str | None
    timestamp: datetime
    categories: list[str]
    source: str | None = None
    source_event_id: str | None = None


@dataclass
class SequenceAnalysis:
    is_sequenced: bool
    sequence_type: str | None
    confidence: float
    adjustment: float
    is_benign: bool
    reason: str


class SequenceDetector:
    """Detects event sequences and patterns for temporal correlation."""

    SEQUENCE_WINDOW_MINUTES = 15
    LOITERING_MIN_EVENTS = 3
    LOITERING_MIN_DURATION_SECONDS = 300
    LOITERING_MAX_DURATION_SECONDS = 1800

    async def get_recent_events(
        self,
        db: AsyncSession,
        stream_id: str,
        window_minutes: int = SEQUENCE_WINDOW_MINUTES,
    ) -> list[Event]:
        since = datetime.utcnow() - timedelta(minutes=window_minutes)
        result = await db.execute(
            select(Event)
            .where(Event.stream_id == stream_id)
            .where(Event.timestamp >= since)
            .order_by(Event.timestamp.asc())
        )
        return list(result.scalars().all())

    async def analyze_sequence(
        self,
        current_event: SequenceEvent,
        recent_events: list[Event],
    ) -> SequenceAnalysis:
        all_events = self._build_event_sequence(current_event, recent_events)
        if len(all_events) < 2:
            return SequenceAnalysis(
                is_sequenced=False,
                sequence_type=None,
                confidence=0.0,
                adjustment=0.0,
                is_benign=False,
                reason="No recent events to analyze",
            )

        sequence_type = self._classify_sequence(all_events)
        if not sequence_type:
            return SequenceAnalysis(
                is_sequenced=False,
                sequence_type=None,
                confidence=0.0,
                adjustment=0.0,
                is_benign=False,
                reason="No known sequence pattern detected",
            )
        return self._get_adjustment(sequence_type, all_events)

    def _build_event_sequence(
        self,
        current_event: SequenceEvent,
        recent_events: list[Event],
    ) -> list[SequenceEvent]:
        events = [self._to_sequence_event(event) for event in recent_events]
        deduped = [
            event
            for event in events
            if not (
                event.event_id == current_event.event_id
                or (
                    current_event.source
                    and current_event.source_event_id
                    and event.source == current_event.source
                    and event.source_event_id == current_event.source_event_id
                )
            )
        ]
        deduped.append(current_event)
        deduped.sort(key=lambda event: event.timestamp)
        return deduped

    def _to_sequence_event(self, event: Event) -> SequenceEvent:
        return SequenceEvent(
            event_id=event.id,
            stream_id=event.stream_id,
            zone=self._event_zone(event),
            timestamp=event.timestamp,
            categories=self._extract_categories(event),
            source=event.source,
            source_event_id=event.source_event_id,
        )

    def _event_zone(self, event: Event) -> str | None:
        if getattr(event, "zone", None):
            return str(event.zone)
        try:
            context = json.loads(event.event_context or "{}")
        except (TypeError, json.JSONDecodeError):
            context = {}
        zone = context.get("zone") if isinstance(context, dict) else None
        return str(zone) if zone else None

    def _classify_sequence(self, events: list[SequenceEvent]) -> str | None:
        if len(events) < 2:
            return None
        if self._is_delivery_pattern(events):
            return "delivery"
        if self._is_intrusion_pattern(events):
            return "intrusion"
        if self._is_resident_pattern(events):
            return "resident"
        if self._is_loitering(events):
            return "loitering"
        return None

    def _is_delivery_pattern(self, events: list[SequenceEvent]) -> bool:
        categories = [event.categories for event in events]
        has_person = any("person" in cats for cats in categories)
        has_package = any("package" in cats for cats in categories)
        if not (has_person and has_package):
            return False
        person_idx = next((i for i, cats in enumerate(categories) if "person" in cats), -1)
        package_idx = next((i for i, cats in enumerate(categories) if "package" in cats), -1)
        return person_idx >= 0 and package_idx >= person_idx

    def _is_intrusion_pattern(self, events: list[SequenceEvent]) -> bool:
        zones = [event.zone for event in events if event.zone]
        if len(zones) < 2:
            return False
        first_interior_idx = next((i for i, zone in enumerate(zones) if zone in INTERIOR_ZONES), None)
        last_perimeter_idx = None
        for i, zone in enumerate(zones):
            if zone in PERIMETER_ZONES:
                last_perimeter_idx = i
        return first_interior_idx is not None and last_perimeter_idx is not None and first_interior_idx > last_perimeter_idx

    def _is_resident_pattern(self, events: list[SequenceEvent]) -> bool:
        resident_paths = [
            ["front_door", "living_room"],
            ["front_door", "kitchen"],
            ["backyard", "living_room"],
            ["garage", "kitchen"],
            ["driveway", "front_door"],
        ]
        zones = [event.zone for event in events if event.zone]
        if len(zones) < 2:
            return False
        return any(self._is_subsequence(path, zones) for path in resident_paths)

    def _is_subsequence(self, pattern: list[str], sequence: list[str]) -> bool:
        pattern_idx = 0
        for zone in sequence:
            if pattern_idx < len(pattern) and zone == pattern[pattern_idx]:
                pattern_idx += 1
        return pattern_idx == len(pattern)

    def _is_loitering(self, events: list[SequenceEvent]) -> bool:
        if len(events) < self.LOITERING_MIN_EVENTS:
            return False
        cameras = set(event.stream_id for event in events)
        if len(cameras) != 1:
            return False
        timestamps = sorted(event.timestamp for event in events)
        time_span = (timestamps[-1] - timestamps[0]).total_seconds()
        return self.LOITERING_MIN_DURATION_SECONDS <= time_span <= self.LOITERING_MAX_DURATION_SECONDS

    def _extract_categories(self, event: Event) -> list[str]:
        try:
            cats = json.loads(event.categories) if isinstance(event.categories, str) else event.categories
        except (json.JSONDecodeError, TypeError):
            cats = []
        return [str(cat).lower() for cat in (cats or [])]

    def _get_adjustment(self, sequence_type: str, events: list[SequenceEvent]) -> SequenceAnalysis:
        adjustments = {
            "delivery": SequenceAnalysis(
                is_sequenced=True,
                sequence_type="delivery",
                confidence=0.8,
                adjustment=-0.25,
                is_benign=True,
                reason="Package delivery sequence detected - typically benign",
            ),
            "resident": SequenceAnalysis(
                is_sequenced=True,
                sequence_type="resident",
                confidence=0.9,
                adjustment=-0.30,
                is_benign=True,
                reason="Matches known resident movement pattern",
            ),
            "loitering": SequenceAnalysis(
                is_sequenced=True,
                sequence_type="loitering",
                confidence=0.7,
                adjustment=0.20,
                is_benign=False,
                reason=f"Prolonged presence detected ({len(events)} events)",
            ),
            "intrusion": SequenceAnalysis(
                is_sequenced=True,
                sequence_type="intrusion",
                confidence=0.85,
                adjustment=0.35,
                is_benign=False,
                reason="Perimeter to interior movement - potential intrusion",
            ),
        }
        return adjustments.get(
            sequence_type,
            SequenceAnalysis(
                is_sequenced=False,
                sequence_type=None,
                confidence=0.0,
                adjustment=0.0,
                is_benign=False,
                reason="Unknown sequence type",
            ),
        )

    async def link_events(
        self,
        db: AsyncSession,
        events: list[Event],
        sequence_type: str,
    ) -> str:
        sequence_id = str(uuid.uuid4())
        for index, event in enumerate(events, start=1):
            event.sequence_id = sequence_id
            event.sequence_position = index
            event.sequence_type = sequence_type
        await db.commit()
        logger.info("Linked %d events into sequence %s (%s)", len(events), sequence_id, sequence_type)
        return sequence_id
