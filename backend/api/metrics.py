"""Metrics API endpoint for monitoring pipeline performance."""

from __future__ import annotations

from fastapi import APIRouter

from backend.metrics import get_metrics

router = APIRouter(tags=["metrics"])


@router.get("/api/metrics")
async def get_metrics_snapshot():
    """
    Export current metrics snapshot for monitoring.
    
    Returns JSON with:
    - Latency percentiles (p50, p95, p99)
    - Throughput counters (1h, 24h, total)
    - Action mix (alert/suppress/uncertain rates)
    - Error counts by type
    - System gauges (active streams, connections)
    """
    metrics = get_metrics()
    return metrics.snapshot()
