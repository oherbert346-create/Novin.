from __future__ import annotations

import time
import threading
import unittest

from backend.metrics import MetricsCollector


class PercentileTests(unittest.TestCase):
    """Tests for MetricsCollector._percentile — core of latency P50/P95/P99."""

    def setUp(self):
        self.mc = MetricsCollector()

    def test_empty_returns_zero(self):
        self.assertEqual(self.mc._percentile([], 0.50), 0.0)
        self.assertEqual(self.mc._percentile([], 0.95), 0.0)
        self.assertEqual(self.mc._percentile([], 0.99), 0.0)

    def test_single_value_returns_that_value_for_all_percentiles(self):
        self.assertEqual(self.mc._percentile([500.0], 0.50), 500.0)
        self.assertEqual(self.mc._percentile([500.0], 0.95), 500.0)
        self.assertEqual(self.mc._percentile([500.0], 0.99), 500.0)

    def test_p50_is_median(self):
        # 10 evenly-spaced values: median is roughly 5th element
        values = [float(i * 100) for i in range(1, 11)]
        result = self.mc._percentile(values, 0.50)
        # idx = int(10 * 0.50) = 5 → values[5] = 600.0
        self.assertEqual(result, 600.0)

    def test_p95_is_near_top(self):
        values = [float(i) for i in range(1, 101)]  # 1..100
        result = self.mc._percentile(values, 0.95)
        # idx = int(100 * 0.95) = 95 → values[95] = 96.0
        self.assertEqual(result, 96.0)

    def test_p99_higher_than_p95_higher_than_p50(self):
        values = [float(i) for i in range(1, 201)]
        p50 = self.mc._percentile(values, 0.50)
        p95 = self.mc._percentile(values, 0.95)
        p99 = self.mc._percentile(values, 0.99)
        self.assertLess(p50, p95)
        self.assertLess(p95, p99)

    def test_all_same_values_returns_that_value(self):
        values = [250.0] * 50
        self.assertEqual(self.mc._percentile(values, 0.50), 250.0)
        self.assertEqual(self.mc._percentile(values, 0.99), 250.0)


class ObserveLatencyTests(unittest.TestCase):
    """Tests for MetricsCollector.observe_latency — records to correct deque."""

    def setUp(self):
        self.mc = MetricsCollector()

    def test_pipeline_latency_appears_in_snapshot(self):
        self.mc.observe_latency("pipeline", 800.0)
        snap = self.mc.snapshot()
        self.assertEqual(snap["latency"]["pipeline_p50_ms"], 800.0)
        self.assertEqual(snap["latency"]["pipeline_p95_ms"], 800.0)

    def test_vision_latency_appears_in_snapshot(self):
        self.mc.observe_latency("vision", 400.0)
        snap = self.mc.snapshot()
        self.assertEqual(snap["latency"]["vision_p50_ms"], 400.0)
        self.assertEqual(snap["latency"]["vision_p95_ms"], 400.0)

    def test_reasoning_latency_appears_in_snapshot(self):
        self.mc.observe_latency("reasoning", 350.0)
        snap = self.mc.snapshot()
        self.assertEqual(snap["latency"]["reasoning_p50_ms"], 350.0)
        self.assertEqual(snap["latency"]["reasoning_p95_ms"], 350.0)

    def test_unknown_metric_name_is_silently_ignored(self):
        # Unknown names should not crash
        self.mc.observe_latency("completely_unknown", 999.0)
        snap = self.mc.snapshot()
        self.assertEqual(snap["latency"]["pipeline_p50_ms"], 0.0)

    def test_multiple_observations_produce_valid_percentiles(self):
        for ms in range(100, 1100, 100):  # 100, 200, ..., 1000
            self.mc.observe_latency("pipeline", float(ms))
        snap = self.mc.snapshot()
        # P50 should be ≤ P95 ≤ P99
        p50 = snap["latency"]["pipeline_p50_ms"]
        p95 = snap["latency"]["pipeline_p95_ms"]
        p99 = snap["latency"]["pipeline_p99_ms"]
        self.assertLessEqual(p50, p95)
        self.assertLessEqual(p95, p99)

    def test_deque_max_size_is_respected(self):
        # Insert 1100 values; only last 1000 should be kept
        for i in range(1100):
            self.mc.observe_latency("pipeline", float(i))
        self.assertEqual(len(self.mc._pipeline_latencies), 1000)


class ThroughputCountingTests(unittest.TestCase):
    """Tests for request counting, windowing, and alert-rate calculation."""

    def setUp(self):
        self.mc = MetricsCollector()

    def test_increment_request_increments_total(self):
        self.mc.increment_request("suppress")
        self.mc.increment_request("suppress")
        self.mc.increment_request("alert")
        snap = self.mc.snapshot()
        self.assertEqual(snap["throughput"]["requests_total"], 3)

    def test_action_breakdown_in_snapshot(self):
        self.mc.increment_request("alert")
        self.mc.increment_request("suppress")
        self.mc.increment_request("uncertain")
        snap = self.mc.snapshot()
        self.assertEqual(snap["actions"]["alert_1h"], 1)
        self.assertEqual(snap["actions"]["suppress_1h"], 1)
        self.assertEqual(snap["actions"]["uncertain_1h"], 1)

    def test_alert_rate_1h_is_percentage(self):
        # 2 alerts out of 4 total = 50%
        for _ in range(2):
            self.mc.increment_request("alert")
        for _ in range(2):
            self.mc.increment_request("suppress")
        snap = self.mc.snapshot()
        self.assertAlmostEqual(snap["actions"]["alert_rate_1h"], 50.0, places=0)

    def test_alert_rate_zero_when_no_alerts(self):
        self.mc.increment_request("suppress")
        snap = self.mc.snapshot()
        self.assertEqual(snap["actions"]["alert_rate_1h"], 0.0)

    def test_requests_1h_includes_all_recent_requests(self):
        for _ in range(10):
            self.mc.increment_request("suppress")
        snap = self.mc.snapshot()
        self.assertEqual(snap["throughput"]["requests_1h"], 10)

    def test_old_requests_excluded_from_1h_window(self):
        # Directly inject an old timestamped entry
        old_ts = time.time() - 7300  # 2+ hours ago
        self.mc._requests.appendleft((old_ts, "suppress"))
        self.mc.increment_request("alert")
        snap = self.mc.snapshot()
        # Only the fresh alert should appear in the 1h window
        self.assertEqual(snap["throughput"]["requests_1h"], 1)

    def test_requests_total_counts_all_including_old(self):
        old_ts = time.time() - 86401  # More than 24h ago
        self.mc._requests.appendleft((old_ts, "suppress"))
        self.mc.increment_request("alert")
        snap = self.mc.snapshot()
        # Both the old entry and the new one appear in requests_total
        self.assertEqual(snap["throughput"]["requests_total"], 2)

    def test_empty_state_returns_zero_counts(self):
        snap = self.mc.snapshot()
        self.assertEqual(snap["throughput"]["requests_total"], 0)
        self.assertEqual(snap["throughput"]["requests_1h"], 0)
        self.assertEqual(snap["actions"]["alert_rate_1h"], 0.0)


class ErrorTrackingTests(unittest.TestCase):
    """Tests for error counter and windowed error reporting."""

    def setUp(self):
        self.mc = MetricsCollector()

    def test_increment_error_tracked_in_snapshot(self):
        self.mc.increment_error("async_ingest_failure")
        snap = self.mc.snapshot()
        self.assertEqual(snap["errors"]["total_1h"], 1)

    def test_multiple_error_types_tracked_separately(self):
        self.mc.increment_error("vision_timeout")
        self.mc.increment_error("reasoning_timeout")
        self.mc.increment_error("async_ingest_failure")
        snap = self.mc.snapshot()
        self.assertEqual(snap["errors"]["total_1h"], 3)
        by_type = snap["errors"]["by_type_1h"]
        self.assertIn("vision_timeout", by_type)
        self.assertIn("reasoning_timeout", by_type)
        self.assertIn("async_ingest_failure", by_type)

    def test_old_errors_excluded_from_1h_window(self):
        old_ts = time.time() - 7300
        self.mc._errors.appendleft((old_ts, "old_error"))
        self.mc.increment_error("new_error")
        snap = self.mc.snapshot()
        self.assertEqual(snap["errors"]["total_1h"], 1)

    def test_error_count_zero_by_default(self):
        snap = self.mc.snapshot()
        self.assertEqual(snap["errors"]["total_1h"], 0)


class GaugeTests(unittest.TestCase):
    """Tests for gauge (active_streams, ws_connections) in MetricsCollector."""

    def setUp(self):
        self.mc = MetricsCollector()

    def test_set_active_streams_reflected_in_snapshot(self):
        self.mc.set_gauge("active_streams", 5)
        snap = self.mc.snapshot()
        self.assertEqual(snap["system"]["active_streams"], 5)

    def test_set_ws_connections_reflected_in_snapshot(self):
        self.mc.set_gauge("ws_connections", 3)
        snap = self.mc.snapshot()
        self.assertEqual(snap["system"]["ws_connections"], 3)

    def test_gauges_start_at_zero(self):
        snap = self.mc.snapshot()
        self.assertEqual(snap["system"]["active_streams"], 0)
        self.assertEqual(snap["system"]["ws_connections"], 0)

    def test_unknown_gauge_name_silently_ignored(self):
        self.mc.set_gauge("unicorn_count", 42)
        snap = self.mc.snapshot()
        self.assertEqual(snap["system"]["active_streams"], 0)


class SnapshotShapeTests(unittest.TestCase):
    """Tests for MetricsCollector.snapshot — shape and key completeness."""

    def setUp(self):
        self.mc = MetricsCollector()

    def test_snapshot_has_latency_section(self):
        snap = self.mc.snapshot()
        self.assertIn("latency", snap)

    def test_latency_section_has_all_percentile_keys(self):
        snap = self.mc.snapshot()
        latency = snap["latency"]
        self.assertIn("pipeline_p50_ms", latency)
        self.assertIn("pipeline_p95_ms", latency)
        self.assertIn("pipeline_p99_ms", latency)
        self.assertIn("vision_p50_ms", latency)
        self.assertIn("vision_p95_ms", latency)
        self.assertIn("reasoning_p50_ms", latency)
        self.assertIn("reasoning_p95_ms", latency)

    def test_snapshot_has_throughput_section(self):
        snap = self.mc.snapshot()
        self.assertIn("throughput", snap)
        self.assertIn("requests_1h", snap["throughput"])
        self.assertIn("requests_24h", snap["throughput"])
        self.assertIn("requests_total", snap["throughput"])

    def test_snapshot_has_actions_section(self):
        snap = self.mc.snapshot()
        self.assertIn("actions", snap)
        self.assertIn("alert_rate_1h", snap["actions"])

    def test_snapshot_has_errors_section(self):
        snap = self.mc.snapshot()
        self.assertIn("errors", snap)
        self.assertIn("total_1h", snap["errors"])
        self.assertIn("total_24h", snap["errors"])

    def test_snapshot_has_system_section(self):
        snap = self.mc.snapshot()
        self.assertIn("system", snap)
        self.assertIn("active_streams", snap["system"])
        self.assertIn("ws_connections", snap["system"])

    def test_latency_values_are_floats(self):
        self.mc.observe_latency("pipeline", 123.456)
        snap = self.mc.snapshot()
        for key in ("pipeline_p50_ms", "pipeline_p95_ms", "pipeline_p99_ms"):
            self.assertIsInstance(snap["latency"][key], float)

    def test_latency_values_rounded_to_one_decimal(self):
        self.mc.observe_latency("pipeline", 123.456789)
        snap = self.mc.snapshot()
        # Should be rounded to 1 decimal place
        val = snap["latency"]["pipeline_p50_ms"]
        self.assertEqual(round(val, 1), val)


class ConcurrencyTests(unittest.TestCase):
    """Tests for thread-safety of MetricsCollector."""

    def setUp(self):
        self.mc = MetricsCollector()

    def test_concurrent_observe_latency_does_not_crash(self):
        errors = []

        def record():
            try:
                for i in range(50):
                    self.mc.observe_latency("pipeline", float(i))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=record) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")

    def test_concurrent_increment_request_does_not_crash(self):
        errors = []

        def record():
            try:
                for _ in range(50):
                    self.mc.increment_request("suppress")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=record) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")
        snap = self.mc.snapshot()
        self.assertEqual(snap["throughput"]["requests_total"], 500)

    def test_concurrent_snapshot_does_not_crash(self):
        """snapshot() under concurrent writes should not raise."""
        errors = []

        def writer():
            try:
                for i in range(30):
                    self.mc.observe_latency("pipeline", float(i))
                    self.mc.increment_request("alert")
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                for _ in range(20):
                    self.mc.snapshot()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(5)] + [
            threading.Thread(target=reader) for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")


class LatencyBudgetComplianceTests(unittest.TestCase):
    """Tests verifying MetricsCollector can express SLA compliance.

    The production SLA from policy.py is:
        pipeline_p95 < 3000 ms
        vision_p95  < 1200 ms
        reasoning_p95 < 1200 ms
    These tests drive values into the collector and assert the snapshot
    reflects compliance or violation correctly.
    """

    PIPELINE_SLA_MS = 3000.0
    VISION_SLA_MS = 1200.0
    REASONING_SLA_MS = 1200.0

    def setUp(self):
        self.mc = MetricsCollector()

    def test_latency_within_sla_shows_compliant_p95(self):
        # All measurements well under SLA
        for _ in range(20):
            self.mc.observe_latency("pipeline", 1200.0)
            self.mc.observe_latency("vision", 400.0)
            self.mc.observe_latency("reasoning", 500.0)
        snap = self.mc.snapshot()
        self.assertLess(snap["latency"]["pipeline_p95_ms"], self.PIPELINE_SLA_MS)
        self.assertLess(snap["latency"]["vision_p95_ms"], self.VISION_SLA_MS)
        self.assertLess(snap["latency"]["reasoning_p95_ms"], self.REASONING_SLA_MS)

    def test_latency_exceeding_sla_shows_violation(self):
        # Record 20 measurements all above SLA
        for _ in range(20):
            self.mc.observe_latency("pipeline", 4000.0)
            self.mc.observe_latency("vision", 2000.0)
            self.mc.observe_latency("reasoning", 1500.0)
        snap = self.mc.snapshot()
        self.assertGreater(snap["latency"]["pipeline_p95_ms"], self.PIPELINE_SLA_MS)
        self.assertGreater(snap["latency"]["vision_p95_ms"], self.VISION_SLA_MS)
        self.assertGreater(snap["latency"]["reasoning_p95_ms"], self.REASONING_SLA_MS)

    def test_p95_above_median_for_mixed_latencies(self):
        """Fast median, slow tail — P95 should reflect the outliers."""
        for _ in range(90):
            self.mc.observe_latency("pipeline", 500.0)   # 90 fast samples
        for _ in range(10):
            self.mc.observe_latency("pipeline", 5000.0)  # 10 slow outliers
        snap = self.mc.snapshot()
        p50 = snap["latency"]["pipeline_p50_ms"]
        p95 = snap["latency"]["pipeline_p95_ms"]
        self.assertEqual(p50, 500.0)
        self.assertGreater(p95, 500.0)  # Tail captured in P95


if __name__ == "__main__":
    unittest.main()
