from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agent.pipeline import _rebuild_routing
from backend.agent.sequence import SequenceDetector, SequenceEvent
from backend.agent.schedule import ScheduleLearner
from backend.models.db import HomeSchedule
from backend.models.schemas import MachineRouting, Verdict


def _hourly_distribution(quiet_hours: set[int]) -> dict[str, float]:
    return {
        str(hour): 0.0 if hour in quiet_hours else 10.0
        for hour in range(24)
    }


class TestSequenceDetector:
    def test_appends_current_event_and_detects_delivery(self):
        detector = SequenceDetector()
        prior = MagicMock(
            id="evt-1",
            stream_id="cam-front",
            zone="front_door",
            categories='["person"]',
            timestamp=datetime.utcnow() - timedelta(minutes=2),
            source="frigate",
            source_event_id="src-1",
            event_context="{}",
        )
        current = SequenceEvent(
            event_id="evt-2",
            stream_id="cam-front",
            zone="front_door",
            timestamp=datetime.utcnow(),
            categories=["package"],
            source="frigate",
            source_event_id="src-2",
        )

        sequence = detector._build_event_sequence(current, [prior])

        assert [event.event_id for event in sequence] == ["evt-1", "evt-2"]
        assert detector._classify_sequence(sequence) == "delivery"

    def test_zone_fallback_from_event_context(self):
        detector = SequenceDetector()
        event = MagicMock(zone=None, event_context='{"zone":"living_room"}')
        assert detector._event_zone(event) == "living_room"

    def test_intrusion_pattern_uses_zone_progression(self):
        detector = SequenceDetector()
        events = [
            SequenceEvent("1", "cam-yard", "backyard", datetime.utcnow(), ["person"]),
            SequenceEvent("2", "cam-hall", "living_room", datetime.utcnow(), ["person"]),
        ]
        assert detector._classify_sequence(events) == "intrusion"

    def test_resident_pattern_uses_zone_progression(self):
        detector = SequenceDetector()
        events = [
            SequenceEvent("1", "cam-drive", "driveway", datetime.utcnow(), ["person"]),
            SequenceEvent("2", "cam-front", "front_door", datetime.utcnow(), ["person"]),
        ]
        assert detector._classify_sequence(events) == "resident"


@pytest.mark.asyncio
async def test_schedule_refresh_if_due_learns_when_missing():
    learner = ScheduleLearner()
    db = AsyncMock()
    with patch.object(learner, "_get_schedule", return_value=None), patch.object(
        learner, "has_sufficient_data", AsyncMock(return_value=True)
    ), patch.object(
        learner, "learn_schedule", AsyncMock(return_value="learned")
    ) as mock_learn:
        result = await learner.refresh_schedule_if_due(db, "home")
    assert result == "learned"
    mock_learn.assert_awaited_once_with(db, "home")


@pytest.mark.asyncio
async def test_schedule_refresh_if_due_skips_fresh_schedule():
    learner = ScheduleLearner()
    db = AsyncMock()
    schedule = HomeSchedule(
        site_id="home",
        events_analyzed=80,
        last_updated=datetime.utcnow(),
    )
    with patch.object(learner, "_get_schedule", return_value=schedule), patch.object(
        learner, "_get_all_events", AsyncMock(return_value=[object()] * 85)
    ), patch.object(learner, "learn_schedule", AsyncMock()) as mock_learn:
        result = await learner.refresh_schedule_if_due(db, "home")
    assert result is schedule
    mock_learn.assert_not_awaited()


def test_find_quiet_hours_handles_overnight_window():
    learner = ScheduleLearner()
    quiet = {23, 0, 1, 2, 3, 4, 5}
    assert learner._find_quiet_hours(_hourly_distribution(quiet)) == (23, 5)


def test_find_quiet_hours_handles_daytime_window():
    learner = ScheduleLearner()
    quiet = {9, 10, 11}
    assert learner._find_quiet_hours(_hourly_distribution(quiet)) == (9, 11)


def test_find_quiet_hours_prefers_longest_disjoint_window():
    learner = ScheduleLearner()
    quiet = {1, 2, 3, 14, 15}
    assert learner._find_quiet_hours(_hourly_distribution(quiet)) == (1, 3)


def test_find_quiet_hours_prefers_midnight_on_tie():
    learner = ScheduleLearner()
    quiet = {23, 0, 1, 10, 11, 12}
    assert learner._find_quiet_hours(_hourly_distribution(quiet)) == (23, 1)


def test_rebuild_routing_clears_immediate_notification_when_suppressed():
    verdict = SimpleNamespace(
        timestamp=datetime(2026, 3, 8, 14, 0),
        routing=MachineRouting(
            is_threat=True,
            action="alert",
            risk_level="high",
            severity="high",
            categories=["person", "intrusion"],
        ),
    )
    routing = _rebuild_routing(
        verdict,
        stream_meta=SimpleNamespace(zone="front_door"),
        alert_signal=0.2,
    )
    assert routing.action == "suppress"
    assert routing.notification_policy != "immediate"


def test_rebuild_routing_escalates_consistently():
    verdict = SimpleNamespace(
        timestamp=datetime(2026, 3, 8, 2, 0),
        routing=MachineRouting(
            is_threat=True,
            action="suppress",
            risk_level="low",
            severity="medium",
            categories=["person", "intrusion"],
        ),
    )
    routing = _rebuild_routing(
        verdict,
        stream_meta=SimpleNamespace(zone="backyard"),
        alert_signal=0.95,
    )
    assert routing.action == "alert"
    assert routing.risk_level == "high"
    assert routing.notification_policy == "immediate"


def test_rebuild_routing_uses_preserved_medium_severity_for_suppressed_threat():
    verdict = SimpleNamespace(
        timestamp=datetime(2026, 3, 8, 14, 0),
        routing=MachineRouting(
            is_threat=True,
            action="suppress",
            risk_level="low",
            severity="medium",
            categories=["person", "intrusion"],
        ),
    )
    routing = _rebuild_routing(
        verdict,
        stream_meta=SimpleNamespace(zone="side_gate"),
        alert_signal=0.2,
    )
    assert routing.action == "suppress"
    assert routing.risk_level == "medium"
