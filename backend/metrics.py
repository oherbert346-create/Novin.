"""In-memory metrics collector for monitoring pipeline performance."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class MetricsCollector:
    """Thread-safe in-memory metrics aggregator with windowed tracking."""
    
    # Latency tracking (deque for percentile calculation)
    _pipeline_latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    _vision_latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    _reasoning_latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    
    # Counters with timestamps for windowing
    _requests: Deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=10000))  # (timestamp, action)
    _errors: Deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=10000))  # (timestamp, error_type)

    # Frame drop counter (keyed by stream_id)
    _frame_drops: dict[str, int] = field(default_factory=dict)
    
    # Gauges (current values)
    _active_streams: int = 0
    _ws_connections: int = 0
    
    # Lock for thread safety
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def observe_latency(self, metric_name: str, value_ms: float) -> None:
        """Record a latency measurement."""
        with self._lock:
            if metric_name == "pipeline":
                self._pipeline_latencies.append(value_ms)
            elif metric_name == "vision":
                self._vision_latencies.append(value_ms)
            elif metric_name == "reasoning":
                self._reasoning_latencies.append(value_ms)
    
    def increment_request(self, action: str) -> None:
        """Increment request counter with action type."""
        with self._lock:
            self._requests.append((time.time(), action))
    
    def increment_error(self, error_type: str) -> None:
        """Increment error counter with error type."""
        with self._lock:
            self._errors.append((time.time(), error_type))
    
    def increment_frame_drop(self, stream_id: str) -> None:
        """Increment dropped-frame counter for a stream."""
        with self._lock:
            self._frame_drops[stream_id] = self._frame_drops.get(stream_id, 0) + 1

    def frame_drop_counts(self) -> dict[str, int]:
        """Return a snapshot of per-stream frame drop counts."""
        with self._lock:
            return dict(self._frame_drops)

    def set_gauge(self, gauge_name: str, value: int) -> None:
        """Set a gauge value."""
        with self._lock:
            if gauge_name == "active_streams":
                self._active_streams = value
            elif gauge_name == "ws_connections":
                self._ws_connections = value
    
    def _percentile(self, values: list[float], p: float) -> float:
        """Calculate percentile from sorted values."""
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * p)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]
    
    def _count_window(self, items: Deque[tuple[float, str]], window_seconds: int) -> dict[str, int]:
        """Count items in time window, grouped by type."""
        cutoff = time.time() - window_seconds
        counts: dict[str, int] = defaultdict(int)
        total = 0
        for ts, item_type in items:
            if ts >= cutoff:
                counts[item_type] += 1
                total += 1
        counts["total"] = total
        return dict(counts)
    
    def snapshot(self) -> dict:
        """Get current metrics snapshot."""
        with self._lock:
            pipeline_vals = list(self._pipeline_latencies)
            vision_vals = list(self._vision_latencies)
            reasoning_vals = list(self._reasoning_latencies)
            
            requests_1h = self._count_window(self._requests, 3600)
            requests_24h = self._count_window(self._requests, 86400)
            requests_total = dict(self._count_window(self._requests, float('inf')))
            
            errors_1h = self._count_window(self._errors, 3600)
            errors_24h = self._count_window(self._errors, 86400)
            
            return {
                "latency": {
                    "pipeline_p50_ms": round(self._percentile(pipeline_vals, 0.50), 1),
                    "pipeline_p95_ms": round(self._percentile(pipeline_vals, 0.95), 1),
                    "pipeline_p99_ms": round(self._percentile(pipeline_vals, 0.99), 1),
                    "vision_p50_ms": round(self._percentile(vision_vals, 0.50), 1),
                    "vision_p95_ms": round(self._percentile(vision_vals, 0.95), 1),
                    "reasoning_p50_ms": round(self._percentile(reasoning_vals, 0.50), 1),
                    "reasoning_p95_ms": round(self._percentile(reasoning_vals, 0.95), 1),
                },
                "throughput": {
                    "requests_1h": requests_1h.get("total", 0),
                    "requests_24h": requests_24h.get("total", 0),
                    "requests_total": requests_total.get("total", 0),
                },
                "actions": {
                    "alert_1h": requests_1h.get("alert", 0),
                    "suppress_1h": requests_1h.get("suppress", 0),
                    "uncertain_1h": requests_1h.get("uncertain", 0),
                    "alert_24h": requests_24h.get("alert", 0),
                    "suppress_24h": requests_24h.get("suppress", 0),
                    "uncertain_24h": requests_24h.get("uncertain", 0),
                    "alert_rate_1h": round(
                        requests_1h.get("alert", 0) / max(requests_1h.get("total", 1), 1) * 100, 1
                    ),
                },
                "errors": {
                    "total_1h": errors_1h.get("total", 0),
                    "total_24h": errors_24h.get("total", 0),
                    "by_type_1h": {k: v for k, v in errors_1h.items() if k != "total"},
                },
                "system": {
                    "active_streams": self._active_streams,
                    "ws_connections": self._ws_connections,
                },
            }


# Global singleton
_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    """Get global metrics collector instance."""
    return _metrics
