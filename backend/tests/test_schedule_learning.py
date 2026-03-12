from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from backend.agent.schedule import ScheduleAdjustment, ScheduleLearner


def _make_event_with_timestamp(ts: datetime) -> MagicMock:
    """Create a minimal mock Event with just a timestamp."""
    obj = MagicMock()
    obj.timestamp = ts
    return obj


class ComputeHourlyDistributionTests(unittest.TestCase):
    """Tests for ScheduleLearner._compute_hourly_distribution."""

    def setUp(self):
        self.learner = ScheduleLearner()

    def _make_events_at_hours(self, hours: list[int]) -> list[MagicMock]:
        return [_make_event_with_timestamp(datetime(2024, 6, 1, h, 0, 0)) for h in hours]

    def test_all_hours_present_in_output(self):
        events = self._make_events_at_hours([0, 6, 12, 18])
        result = self.learner._compute_hourly_distribution(events)
        for h in range(24):
            self.assertIn(str(h), result)

    def test_percentages_sum_to_100(self):
        events = self._make_events_at_hours(list(range(24)))
        result = self.learner._compute_hourly_distribution(events)
        total = sum(result.values())
        self.assertAlmostEqual(total, 100.0, places=5)

    def test_concentrated_hour_has_highest_percentage(self):
        # All events at hour 10
        events = self._make_events_at_hours([10] * 10)
        result = self.learner._compute_hourly_distribution(events)
        self.assertAlmostEqual(result["10"], 100.0, places=5)
        for h in range(24):
            if h != 10:
                self.assertAlmostEqual(result[str(h)], 0.0, places=5)

    def test_empty_events_returns_all_zeros(self):
        result = self.learner._compute_hourly_distribution([])
        self.assertTrue(all(v == 0.0 for v in result.values()))

    def test_two_equal_hours_each_have_50_percent(self):
        events = self._make_events_at_hours([8, 20])
        result = self.learner._compute_hourly_distribution(events)
        self.assertAlmostEqual(result["8"], 50.0, places=5)
        self.assertAlmostEqual(result["20"], 50.0, places=5)


class FindQuietHoursTests(unittest.TestCase):
    """Tests for ScheduleLearner._find_quiet_hours — finds the longest block of low-activity hours."""

    def setUp(self):
        self.learner = ScheduleLearner()

    def _make_dist(self, active_hours: list[int], total_hours: int = 24) -> dict[str, float]:
        """Create hourly distribution with active_hours at 10% each and others at 0%."""
        dist: dict[str, float] = {}
        active_pct = 100.0 / max(len(active_hours), 1)
        for h in range(total_hours):
            dist[str(h)] = active_pct if h in active_hours else 0.0
        return dist

    def test_all_zeros_no_block_start_found(self):
        # When all 24 hours are quiet, every hour has a quiet predecessor, so
        # the block-start detection cannot identify a distinct start point → None
        dist = {str(h): 0.0 for h in range(24)}
        result = self.learner._find_quiet_hours(dist)
        self.assertIsNone(result)

    def test_all_active_returns_none(self):
        # All hours above quiet threshold (5%)
        dist = {str(h): 10.0 for h in range(24)}
        result = self.learner._find_quiet_hours(dist)
        # No quiet hours when all hours are busy
        self.assertIsNone(result)

    def test_nighttime_quiet_block_detected(self):
        # Activity only during hours 8-21 (14 hours); 22-7 should be quiet
        active = list(range(8, 22))
        dist = self._make_dist(active)
        result = self.learner._find_quiet_hours(dist)
        self.assertIsNotNone(result)
        start, end = result
        # The quiet block should span somewhere in 22-7
        # The block should not start inside the active window
        self.assertNotIn(start, range(8, 22))

    def test_returns_tuple_of_two_ints(self):
        dist = self._make_dist(list(range(7, 23)))  # Active 7-22, quiet 23-6
        result = self.learner._find_quiet_hours(dist)
        if result is not None:
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 2)
            self.assertIsInstance(result[0], int)
            self.assertIsInstance(result[1], int)


class FindPeakHoursTests(unittest.TestCase):
    """Tests for ScheduleLearner._find_peak_hours — identifies hours above peak threshold."""

    def setUp(self):
        self.learner = ScheduleLearner()

    def test_hour_above_threshold_returned(self):
        dist = {str(h): 0.0 for h in range(24)}
        dist["10"] = 35.0  # Above PEAK_HOUR_THRESHOLD (30%)
        result = self.learner._find_peak_hours(dist)
        self.assertIn(10, result)

    def test_hour_below_threshold_not_returned(self):
        dist = {str(h): 10.0 for h in range(24)}  # All at 10%, below 30%
        result = self.learner._find_peak_hours(dist)
        self.assertEqual(result, [])

    def test_multiple_peak_hours(self):
        dist = {str(h): 0.0 for h in range(24)}
        dist["8"] = 32.0
        dist["17"] = 31.0
        result = self.learner._find_peak_hours(dist)
        self.assertIn(8, result)
        self.assertIn(17, result)


class InHourRangeTests(unittest.TestCase):
    """Tests for ScheduleLearner._in_hour_range — handles normal and overnight ranges."""

    def setUp(self):
        self.learner = ScheduleLearner()

    def test_normal_range_hour_inside(self):
        self.assertTrue(self.learner._in_hour_range(12, 9, 17))

    def test_normal_range_hour_at_start(self):
        self.assertTrue(self.learner._in_hour_range(9, 9, 17))

    def test_normal_range_hour_at_end(self):
        self.assertTrue(self.learner._in_hour_range(17, 9, 17))

    def test_normal_range_hour_outside(self):
        self.assertFalse(self.learner._in_hour_range(8, 9, 17))
        self.assertFalse(self.learner._in_hour_range(18, 9, 17))

    def test_overnight_range_hour_after_start(self):
        # Overnight: 22-6 (wraps past midnight)
        self.assertTrue(self.learner._in_hour_range(23, 22, 6))
        self.assertTrue(self.learner._in_hour_range(0, 22, 6))
        self.assertTrue(self.learner._in_hour_range(4, 22, 6))
        self.assertTrue(self.learner._in_hour_range(6, 22, 6))

    def test_overnight_range_hour_outside(self):
        self.assertFalse(self.learner._in_hour_range(10, 22, 6))
        self.assertFalse(self.learner._in_hour_range(21, 22, 6))


class ScheduleAdjustmentLogicTests(unittest.IsolatedAsyncioTestCase):
    """Tests for ScheduleLearner.get_schedule_adjustment — uses mock DB and schedule."""

    def setUp(self):
        self.learner = ScheduleLearner()

    def _make_schedule(
        self,
        quiet_start: int | None,
        quiet_end: int | None,
        hourly_dist: dict[str, float] | None = None,
    ) -> MagicMock:
        schedule = MagicMock()
        schedule.quiet_hours_start = quiet_start
        schedule.quiet_hours_end = quiet_end
        schedule.typical_arrivals = __import__("json").dumps(
            hourly_dist or {str(h): 0.0 for h in range(24)}
        )
        return schedule

    async def test_no_schedule_returns_zero_adjustment(self):
        db = MagicMock()
        db.execute = MagicMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        # Patch _get_schedule to return None
        self.learner._get_schedule = MagicMock(return_value=None)
        # Need async mock
        import asyncio
        async def fake_get_schedule(db, site_id):
            return None
        self.learner._get_schedule = fake_get_schedule

        result = await self.learner.get_schedule_adjustment(db, "home-1", datetime(2024, 6, 1, 3, 0, 0))
        self.assertEqual(result.adjustment, 0.0)
        self.assertEqual(result.reason, "No schedule learned yet")

    async def test_quiet_hours_returns_positive_adjustment(self):
        # Hour 3 is inside quiet range 22-6
        schedule = self._make_schedule(quiet_start=22, quiet_end=6)

        async def fake_get_schedule(db, site_id):
            return schedule

        self.learner._get_schedule = fake_get_schedule
        db = MagicMock()

        result = await self.learner.get_schedule_adjustment(db, "home-1", datetime(2024, 6, 1, 3, 0, 0))
        self.assertGreater(result.adjustment, 0)
        self.assertFalse(result.is_expected)

    async def test_peak_hour_returns_negative_adjustment(self):
        # Hour 9 is in peak hours (>30%)
        dist = {str(h): 0.0 for h in range(24)}
        dist["9"] = 35.0
        schedule = self._make_schedule(quiet_start=None, quiet_end=None, hourly_dist=dist)

        async def fake_get_schedule(db, site_id):
            return schedule

        self.learner._get_schedule = fake_get_schedule
        db = MagicMock()

        result = await self.learner.get_schedule_adjustment(db, "home-1", datetime(2024, 6, 1, 9, 0, 0))
        self.assertLess(result.adjustment, 0)
        self.assertTrue(result.is_expected)

    async def test_normal_hour_returns_zero_adjustment(self):
        # Hour 14 is not peak and not quiet
        dist = {str(h): 4.0 for h in range(24)}  # Even distribution, no peak
        schedule = self._make_schedule(quiet_start=23, quiet_end=5, hourly_dist=dist)

        async def fake_get_schedule(db, site_id):
            return schedule

        self.learner._get_schedule = fake_get_schedule
        db = MagicMock()

        result = await self.learner.get_schedule_adjustment(db, "home-1", datetime(2024, 6, 1, 14, 0, 0))
        self.assertEqual(result.adjustment, 0.0)
        self.assertTrue(result.is_expected)


class ComputeDailyDistributionTests(unittest.TestCase):
    """Tests for ScheduleLearner._compute_daily_distribution."""

    def setUp(self):
        self.learner = ScheduleLearner()

    def test_all_weekdays_present_in_output(self):
        events = [_make_event_with_timestamp(datetime(2024, 6, 3 + i, 12, 0, 0)) for i in range(7)]
        result = self.learner._compute_daily_distribution(events)
        expected_days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
        self.assertEqual(set(result.keys()), expected_days)

    def test_percentages_sum_to_100(self):
        events = [_make_event_with_timestamp(datetime(2024, 6, 3 + i, 12, 0, 0)) for i in range(7)]
        result = self.learner._compute_daily_distribution(events)
        self.assertAlmostEqual(sum(result.values()), 100.0, places=5)

    def test_empty_events_returns_all_zeros(self):
        result = self.learner._compute_daily_distribution([])
        self.assertTrue(all(v == 0.0 for v in result.values()))


if __name__ == "__main__":
    unittest.main()
