from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from backend.agent.sequence import SequenceDetector, SequenceEvent


def _make_event(
    *,
    event_id: str,
    stream_id: str = "stream-1",
    zone: str | None = None,
    timestamp: datetime,
    categories: list[str],
    source: str | None = None,
    source_event_id: str | None = None,
) -> SequenceEvent:
    return SequenceEvent(
        event_id=event_id,
        stream_id=stream_id,
        zone=zone,
        timestamp=timestamp,
        categories=categories,
        source=source,
        source_event_id=source_event_id,
    )


def _make_db_event(
    *,
    event_id: str,
    stream_id: str = "stream-1",
    zone: str | None = None,
    timestamp: datetime,
    categories: list[str],
    source: str | None = None,
    source_event_id: str | None = None,
) -> Any:
    """Build a minimal mock object that looks like an Event ORM row."""
    obj = MagicMock()
    obj.id = event_id
    obj.stream_id = stream_id
    obj.zone = zone
    obj.timestamp = timestamp
    obj.categories = json.dumps(categories)
    obj.source = source
    obj.source_event_id = source_event_id
    obj.event_context = "{}"
    return obj


_NOW = datetime(2024, 6, 1, 14, 0, 0)


class DeliveryPatternTests(unittest.TestCase):
    """Tests for _is_delivery_pattern — person-then-package sequence."""

    def setUp(self):
        self.detector = SequenceDetector()

    def test_person_then_package_is_delivery(self):
        events = [
            _make_event(event_id="e1", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", timestamp=_NOW + timedelta(minutes=2), categories=["package"]),
        ]
        self.assertTrue(self.detector._is_delivery_pattern(events))

    def test_package_without_person_is_not_delivery(self):
        events = [
            _make_event(event_id="e1", timestamp=_NOW, categories=["package"]),
        ]
        self.assertFalse(self.detector._is_delivery_pattern(events))

    def test_person_without_package_is_not_delivery(self):
        events = [
            _make_event(event_id="e1", timestamp=_NOW, categories=["person"]),
        ]
        self.assertFalse(self.detector._is_delivery_pattern(events))

    def test_package_before_person_is_not_delivery(self):
        # package comes first → person_idx > package_idx → not delivery
        events = [
            _make_event(event_id="e1", timestamp=_NOW, categories=["package"]),
            _make_event(event_id="e2", timestamp=_NOW + timedelta(minutes=1), categories=["person"]),
        ]
        self.assertFalse(self.detector._is_delivery_pattern(events))

    def test_simultaneous_person_and_package_is_delivery(self):
        events = [
            _make_event(event_id="e1", timestamp=_NOW, categories=["person", "package"]),
        ]
        self.assertTrue(self.detector._is_delivery_pattern(events))


class IntrusionPatternTests(unittest.TestCase):
    """Tests for _is_intrusion_pattern — perimeter → interior progression."""

    def setUp(self):
        self.detector = SequenceDetector()

    def test_perimeter_then_interior_is_intrusion(self):
        events = [
            _make_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="living_room", timestamp=_NOW + timedelta(minutes=1), categories=["person"]),
        ]
        self.assertTrue(self.detector._is_intrusion_pattern(events))

    def test_interior_only_is_not_intrusion(self):
        events = [
            _make_event(event_id="e1", zone="living_room", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="kitchen", timestamp=_NOW + timedelta(minutes=1), categories=["person"]),
        ]
        self.assertFalse(self.detector._is_intrusion_pattern(events))

    def test_perimeter_only_is_not_intrusion(self):
        events = [
            _make_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="driveway", timestamp=_NOW + timedelta(minutes=1), categories=["person"]),
        ]
        self.assertFalse(self.detector._is_intrusion_pattern(events))

    def test_interior_before_perimeter_is_not_intrusion(self):
        # If interior comes BEFORE perimeter in the sequence, that's egress — not intrusion
        events = [
            _make_event(event_id="e1", zone="living_room", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="front_door", timestamp=_NOW + timedelta(minutes=1), categories=["person"]),
        ]
        self.assertFalse(self.detector._is_intrusion_pattern(events))

    def test_single_event_is_not_intrusion(self):
        events = [
            _make_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"]),
        ]
        self.assertFalse(self.detector._is_intrusion_pattern(events))

    def test_no_zone_events_is_not_intrusion(self):
        events = [
            _make_event(event_id="e1", zone=None, timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone=None, timestamp=_NOW + timedelta(minutes=1), categories=["person"]),
        ]
        self.assertFalse(self.detector._is_intrusion_pattern(events))


class ResidentPatternTests(unittest.TestCase):
    """Tests for _is_resident_pattern — known household movement paths."""

    def setUp(self):
        self.detector = SequenceDetector()

    def test_front_door_to_living_room_is_resident(self):
        events = [
            _make_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="living_room", timestamp=_NOW + timedelta(seconds=30), categories=["person"]),
        ]
        self.assertTrue(self.detector._is_resident_pattern(events))

    def test_front_door_to_kitchen_is_resident(self):
        events = [
            _make_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="kitchen", timestamp=_NOW + timedelta(seconds=30), categories=["person"]),
        ]
        self.assertTrue(self.detector._is_resident_pattern(events))

    def test_driveway_to_front_door_is_resident(self):
        events = [
            _make_event(event_id="e1", zone="driveway", timestamp=_NOW, categories=["vehicle"]),
            _make_event(event_id="e2", zone="front_door", timestamp=_NOW + timedelta(seconds=60), categories=["person"]),
        ]
        self.assertTrue(self.detector._is_resident_pattern(events))

    def test_garage_to_kitchen_is_resident(self):
        events = [
            _make_event(event_id="e1", zone="garage", timestamp=_NOW, categories=["vehicle"]),
            _make_event(event_id="e2", zone="kitchen", timestamp=_NOW + timedelta(seconds=60), categories=["person"]),
        ]
        self.assertTrue(self.detector._is_resident_pattern(events))

    def test_random_zone_sequence_is_not_resident(self):
        events = [
            _make_event(event_id="e1", zone="backyard", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="garage", timestamp=_NOW + timedelta(seconds=30), categories=["person"]),
        ]
        self.assertFalse(self.detector._is_resident_pattern(events))

    def test_single_event_is_not_resident(self):
        events = [
            _make_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"]),
        ]
        self.assertFalse(self.detector._is_resident_pattern(events))


class LoiteringPatternTests(unittest.TestCase):
    """Tests for _is_loitering — repeated events on a single camera within time bounds."""

    def setUp(self):
        self.detector = SequenceDetector()

    def test_enough_events_over_long_duration_is_loitering(self):
        events = [
            _make_event(event_id=f"e{i}", stream_id="stream-1", timestamp=_NOW + timedelta(seconds=i * 120), categories=["person"])
            for i in range(4)
        ]
        # duration = 3 * 120 = 360s (within 300-1800s), 4 events, 1 camera
        self.assertTrue(self.detector._is_loitering(events))

    def test_too_few_events_is_not_loitering(self):
        events = [
            _make_event(event_id="e1", stream_id="stream-1", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", stream_id="stream-1", timestamp=_NOW + timedelta(seconds=400), categories=["person"]),
        ]
        # Only 2 events, min is 3
        self.assertFalse(self.detector._is_loitering(events))

    def test_too_short_duration_is_not_loitering(self):
        events = [
            _make_event(event_id=f"e{i}", stream_id="stream-1", timestamp=_NOW + timedelta(seconds=i * 10), categories=["person"])
            for i in range(4)
        ]
        # duration = 30s < 300s minimum
        self.assertFalse(self.detector._is_loitering(events))

    def test_too_long_duration_is_not_loitering(self):
        events = [
            _make_event(event_id=f"e{i}", stream_id="stream-1", timestamp=_NOW + timedelta(minutes=i * 15), categories=["person"])
            for i in range(4)
        ]
        # duration = 45 minutes = 2700s > 1800s max
        self.assertFalse(self.detector._is_loitering(events))

    def test_multiple_cameras_is_not_loitering(self):
        events = [
            _make_event(event_id="e1", stream_id="stream-1", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", stream_id="stream-2", timestamp=_NOW + timedelta(seconds=200), categories=["person"]),
            _make_event(event_id="e3", stream_id="stream-3", timestamp=_NOW + timedelta(seconds=400), categories=["person"]),
            _make_event(event_id="e4", stream_id="stream-4", timestamp=_NOW + timedelta(seconds=600), categories=["person"]),
        ]
        self.assertFalse(self.detector._is_loitering(events))


class ClassifySequenceTests(unittest.TestCase):
    """Tests for _classify_sequence — identifies the dominant pattern in event list."""

    def setUp(self):
        self.detector = SequenceDetector()

    def test_classifies_delivery(self):
        events = [
            _make_event(event_id="e1", zone="porch", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="porch", timestamp=_NOW + timedelta(minutes=2), categories=["package"]),
        ]
        self.assertEqual(self.detector._classify_sequence(events), "delivery")

    def test_classifies_intrusion(self):
        events = [
            _make_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"]),
            _make_event(event_id="e2", zone="living_room", timestamp=_NOW + timedelta(minutes=1), categories=["person"]),
        ]
        self.assertEqual(self.detector._classify_sequence(events), "intrusion")

    def test_classifies_resident(self):
        events = [
            _make_event(event_id="e1", zone="driveway", timestamp=_NOW, categories=["vehicle"]),
            _make_event(event_id="e2", zone="front_door", timestamp=_NOW + timedelta(seconds=60), categories=["person"]),
        ]
        self.assertEqual(self.detector._classify_sequence(events), "resident")

    def test_classifies_loitering(self):
        events = [
            _make_event(event_id=f"e{i}", stream_id="stream-1", timestamp=_NOW + timedelta(seconds=i * 120), categories=["motion"])
            for i in range(4)
        ]
        self.assertEqual(self.detector._classify_sequence(events), "loitering")

    def test_returns_none_for_unknown_pattern(self):
        events = [
            _make_event(event_id="e1", zone="backyard", timestamp=_NOW, categories=["motion"]),
        ]
        self.assertIsNone(self.detector._classify_sequence(events))

    def test_single_event_returns_none(self):
        events = [
            _make_event(event_id="e1", timestamp=_NOW, categories=["motion"]),
        ]
        self.assertIsNone(self.detector._classify_sequence(events))


class GetAdjustmentTests(unittest.TestCase):
    """Tests for _get_adjustment — returns correct SequenceAnalysis per type."""

    def setUp(self):
        self.detector = SequenceDetector()

    def test_delivery_is_benign_with_negative_adjustment(self):
        events = [_make_event(event_id="e1", timestamp=_NOW, categories=["person"])]
        result = self.detector._get_adjustment("delivery", events)
        self.assertTrue(result.is_benign)
        self.assertLess(result.adjustment, 0)
        self.assertEqual(result.sequence_type, "delivery")

    def test_resident_is_benign_with_strong_negative_adjustment(self):
        events = [_make_event(event_id="e1", timestamp=_NOW, categories=["person"])]
        result = self.detector._get_adjustment("resident", events)
        self.assertTrue(result.is_benign)
        self.assertLessEqual(result.adjustment, -0.25)

    def test_intrusion_is_not_benign_with_positive_adjustment(self):
        events = [_make_event(event_id="e1", timestamp=_NOW, categories=["person"])]
        result = self.detector._get_adjustment("intrusion", events)
        self.assertFalse(result.is_benign)
        self.assertGreater(result.adjustment, 0)

    def test_loitering_is_not_benign_with_positive_adjustment(self):
        events = [_make_event(event_id=f"e{i}", timestamp=_NOW + timedelta(seconds=i), categories=["person"]) for i in range(4)]
        result = self.detector._get_adjustment("loitering", events)
        self.assertFalse(result.is_benign)
        self.assertGreater(result.adjustment, 0)

    def test_unknown_type_returns_zero_adjustment(self):
        events = [_make_event(event_id="e1", timestamp=_NOW, categories=["motion"])]
        result = self.detector._get_adjustment("unknown_type", events)
        self.assertFalse(result.is_sequenced)
        self.assertEqual(result.adjustment, 0.0)


class BuildEventSequenceTests(unittest.TestCase):
    """Tests for _build_event_sequence — deduplication and ordering."""

    def setUp(self):
        self.detector = SequenceDetector()

    def test_deduplicates_current_event_by_id(self):
        current = _make_event(event_id="e2", zone="front_door", timestamp=_NOW + timedelta(minutes=1), categories=["person"])
        db_events = [
            _make_db_event(event_id="e1", zone="driveway", timestamp=_NOW, categories=["vehicle"]),
            _make_db_event(event_id="e2", zone="front_door", timestamp=_NOW + timedelta(minutes=1), categories=["person"]),
        ]
        result = self.detector._build_event_sequence(current, db_events)
        ids = [e.event_id for e in result]
        self.assertEqual(ids.count("e2"), 1)

    def test_result_is_sorted_by_timestamp(self):
        current = _make_event(event_id="e3", zone="living_room", timestamp=_NOW + timedelta(minutes=5), categories=["person"])
        db_events = [
            _make_db_event(event_id="e2", zone="front_door", timestamp=_NOW + timedelta(minutes=2), categories=["person"]),
            _make_db_event(event_id="e1", zone="driveway", timestamp=_NOW, categories=["vehicle"]),
        ]
        result = self.detector._build_event_sequence(current, db_events)
        timestamps = [e.timestamp for e in result]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_deduplicates_by_source_and_source_event_id(self):
        """Events with same source + source_event_id as the current event are deduplicated."""
        current = _make_event(
            event_id="e2",
            zone="front_door",
            timestamp=_NOW + timedelta(minutes=1),
            categories=["person"],
            source="frigate",
            source_event_id="fri-123",
        )
        db_events = [
            _make_db_event(
                event_id="e1",  # Different event_id but same source identity
                zone="front_door",
                timestamp=_NOW,
                categories=["person"],
                source="frigate",
                source_event_id="fri-123",
            ),
        ]
        result = self.detector._build_event_sequence(current, db_events)
        # e1 should be deduplicated because it shares source/source_event_id with current
        source_event_ids = [e.source_event_id for e in result if e.source_event_id]
        self.assertEqual(source_event_ids.count("fri-123"), 1)


class AnalyzeSequenceTests(unittest.IsolatedAsyncioTestCase):
    """Integration tests for analyze_sequence — minimal event lists, no DB needed."""

    async def test_single_event_returns_not_sequenced(self):
        detector = SequenceDetector()
        current = _make_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"])
        result = await detector.analyze_sequence(current, [])
        self.assertFalse(result.is_sequenced)
        self.assertIsNone(result.sequence_type)
        self.assertEqual(result.adjustment, 0.0)

    async def test_delivery_sequence_identified(self):
        detector = SequenceDetector()
        current = _make_event(event_id="e2", zone="porch", timestamp=_NOW + timedelta(minutes=2), categories=["package"])
        db_events = [
            _make_db_event(event_id="e1", zone="porch", timestamp=_NOW, categories=["person"]),
        ]
        result = await detector.analyze_sequence(current, db_events)
        self.assertTrue(result.is_sequenced)
        self.assertEqual(result.sequence_type, "delivery")
        self.assertTrue(result.is_benign)
        self.assertLess(result.adjustment, 0)

    async def test_intrusion_sequence_identified(self):
        detector = SequenceDetector()
        current = _make_event(event_id="e2", zone="living_room", timestamp=_NOW + timedelta(minutes=1), categories=["person"])
        db_events = [
            _make_db_event(event_id="e1", zone="front_door", timestamp=_NOW, categories=["person"]),
        ]
        result = await detector.analyze_sequence(current, db_events)
        self.assertTrue(result.is_sequenced)
        self.assertEqual(result.sequence_type, "intrusion")
        self.assertFalse(result.is_benign)
        self.assertGreater(result.adjustment, 0)


if __name__ == "__main__":
    unittest.main()
