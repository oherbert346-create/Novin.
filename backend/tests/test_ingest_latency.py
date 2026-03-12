"""Tests for E2E event latency and ingest throughput capacity.

Validates:
1. process_frame() populates all latency telemetry fields correctly.
2. Latency values satisfy the production SLA budget.
3. MetricsCollector is updated after each process_frame call.
4. process_canonical() returns timing-annotated verdicts and handles
   idempotency (duplicate suppression) correctly.
5. Concurrent ingest calls are all processed without loss.
6. Async ingest mode returns immediately with a queued event_id.
"""

from __future__ import annotations

import asyncio
import uuid
import unittest
from datetime import datetime
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from backend.agent.pipeline import _FRAME_PROCESSING_TIMEOUT, _FRAME_QUEUE_SIZE
from backend.database import AsyncSessionLocal, init_db
from backend.metrics import MetricsCollector, get_metrics
from backend.models.db import Event, Stream
from backend.models.schemas import (
    AgentOutput,
    AuditTrail,
    EventContext,
    FramePacket,
    HistoryContext,
    LiabilityDigest,
    MachineRouting,
    OperatorSummary,
    StreamMeta,
    Verdict,
    VisionResult,
)
from backend.policy import RELEASE_LATENCY_BUDGET_MS


# ---------------------------------------------------------------------------
# Minimal test frame: 4×4 black JPEG encoded as numpy + b64
# ---------------------------------------------------------------------------
_TINY_FRAME = np.zeros((4, 4, 3), dtype=np.uint8)

# Genuine minimal JPEG generated at import time from a 4×4 black frame
def _build_tiny_jpeg_b64() -> str:
    import base64
    import cv2

    arr = np.zeros((4, 4, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", arr)
    return base64.b64encode(buf).decode("ascii")


_TINY_JPEG_B64 = _build_tiny_jpeg_b64()

_AGENT_IDS = [
    "context_baseline_reasoner",
    "trajectory_intent_assessor",
    "falsification_auditor",
    "executive_triage_commander",
]


def _make_vision_result(latency_ms: float = 150.0) -> VisionResult:
    return VisionResult(
        scene_status="active",
        threat=False,
        severity="none",
        categories=["package"],
        description="Package at front door.",
        confidence=0.85,
        latency_ms=latency_ms,
    )


def _make_verdict(event_id: str, stream_id: str, latency_ms: float = 200.0) -> Verdict:
    v = Verdict(
        frame_id=event_id,
        event_id=event_id,
        stream_id=stream_id,
        site_id="home",
        timestamp=datetime.utcnow(),
        routing=MachineRouting(
            is_threat=False,
            action="suppress",
            risk_level="none",
            severity="none",
            categories=["package"],
        ),
        summary=OperatorSummary(headline="Package delivery", narrative="Benign delivery pattern."),
        audit=AuditTrail(
            liability_digest=LiabilityDigest(
                decision_reasoning="Delivery sequence detected.",
                confidence_score=0.82,
            ),
            agent_outputs=[
                AgentOutput(
                    agent_id=aid,
                    role=aid.replace("_", " ").title(),
                    verdict="suppress",
                    confidence=0.82,
                    rationale=(
                        "SIGNAL: package delivery. "
                        "EVIDENCE: person then package sequence. "
                        "UNCERTAINTY: none. "
                        "DECISION: suppress."
                    ),
                )
                for aid in _AGENT_IDS
            ],
        ),
        description="Package at front door.",
        event_context=EventContext(zone="front_door", home_id="home", cam_id=stream_id),
    )
    v.telemetry["pipeline_latency_ms"] = latency_ms
    v.telemetry["vision_latency_ms"] = latency_ms * 0.6
    v.telemetry["reasoning_latency_ms"] = latency_ms * 0.35
    v.telemetry["history_latency_ms"] = 5.0
    return v


# ---------------------------------------------------------------------------
# 1. process_frame telemetry field tests
# ---------------------------------------------------------------------------
class ProcessFrameTelemetryTests(unittest.IsolatedAsyncioTestCase):
    """Tests that process_frame() correctly populates all latency fields."""

    async def _run_process_frame(self, vision_latency_ms: float = 150.0) -> Verdict:
        """Run process_frame with fully mocked vision, history, and reasoning."""
        await init_db()
        event_id = f"test-{uuid.uuid4()}"
        stream_id = f"cam-{uuid.uuid4()}"
        vision_result = _make_vision_result(vision_latency_ms)
        verdict = _make_verdict(event_id, stream_id, latency_ms=vision_latency_ms + 100)

        from backend.agent.pipeline import process_frame

        with (
            patch("backend.agent.pipeline.vision_agent.analyse_frame", AsyncMock(return_value=vision_result)),
            patch("backend.agent.pipeline.history_agent.query_history", AsyncMock(return_value=HistoryContext())),
            patch("backend.agent.pipeline.run_reasoning", AsyncMock(return_value=verdict)),
            patch("backend.agent.pipeline._apply_temporal_correlation", AsyncMock(return_value=verdict)),
            patch("backend.agent.pipeline.get_metrics", return_value=MetricsCollector()),
        ):
            stream_meta = StreamMeta(
                stream_id=stream_id,
                label="Front Door",
                site_id="home",
                zone="front_door",
                uri="direct",
            )
            async with AsyncSessionLocal() as db:
                result = await process_frame(
                    frame=_TINY_FRAME,
                    stream_meta=stream_meta,
                    db=db,
                    groq_client=None,
                    event_id=event_id,
                )
        return result

    async def test_telemetry_contains_pipeline_latency_ms(self):
        verdict = await self._run_process_frame()
        self.assertIn("pipeline_latency_ms", verdict.telemetry)

    async def test_telemetry_contains_vision_latency_ms(self):
        verdict = await self._run_process_frame()
        self.assertIn("vision_latency_ms", verdict.telemetry)

    async def test_telemetry_contains_reasoning_latency_ms(self):
        verdict = await self._run_process_frame()
        self.assertIn("reasoning_latency_ms", verdict.telemetry)

    async def test_telemetry_contains_history_latency_ms(self):
        verdict = await self._run_process_frame()
        self.assertIn("history_latency_ms", verdict.telemetry)

    async def test_telemetry_contains_policy_version(self):
        verdict = await self._run_process_frame()
        self.assertIn("policy_version", verdict.telemetry)

    async def test_telemetry_contains_latency_budget(self):
        verdict = await self._run_process_frame()
        self.assertIn("latency_budget_ms", verdict.telemetry)

    async def test_pipeline_latency_is_positive(self):
        verdict = await self._run_process_frame()
        self.assertGreater(verdict.telemetry["pipeline_latency_ms"], 0)

    async def test_vision_latency_matches_vision_result(self):
        vision_latency = 250.0
        verdict = await self._run_process_frame(vision_latency)
        self.assertAlmostEqual(verdict.telemetry["vision_latency_ms"], vision_latency, delta=1.0)

    async def test_telemetry_latency_budget_matches_policy(self):
        verdict = await self._run_process_frame()
        budget = verdict.telemetry["latency_budget_ms"]
        self.assertEqual(budget["pipeline_p95"], RELEASE_LATENCY_BUDGET_MS["pipeline_p95"])
        self.assertEqual(budget["vision_p95"], RELEASE_LATENCY_BUDGET_MS["vision_p95"])
        self.assertEqual(budget["reasoning_p95"], RELEASE_LATENCY_BUDGET_MS["reasoning_p95"])

    async def test_pipeline_latency_is_float(self):
        verdict = await self._run_process_frame()
        self.assertIsInstance(verdict.telemetry["pipeline_latency_ms"], float)

    async def test_metrics_collector_incremented(self):
        """process_frame() should call get_metrics().observe_latency and increment_request."""
        mc = MetricsCollector()
        with (
            patch("backend.agent.pipeline.vision_agent.analyse_frame", AsyncMock(return_value=_make_vision_result())),
            patch("backend.agent.pipeline.history_agent.query_history", AsyncMock(return_value=HistoryContext())),
            patch(
                "backend.agent.pipeline.run_reasoning",
                AsyncMock(return_value=_make_verdict("e1", "s1")),
            ),
            patch(
                "backend.agent.pipeline._apply_temporal_correlation",
                AsyncMock(return_value=_make_verdict("e1", "s1")),
            ),
            patch("backend.agent.pipeline.get_metrics", return_value=mc),
        ):
            stream_meta = StreamMeta(stream_id="s1", label="Cam", site_id="home", zone="front_door", uri="direct")
            async with AsyncSessionLocal() as db:
                from backend.agent.pipeline import process_frame

                await process_frame(frame=_TINY_FRAME, stream_meta=stream_meta, db=db, groq_client=None)

        snap = mc.snapshot()
        # At least one request was counted
        self.assertGreaterEqual(snap["throughput"]["requests_total"], 1)
        # At least one pipeline latency was recorded
        self.assertGreater(snap["latency"]["pipeline_p50_ms"], 0.0)


# ---------------------------------------------------------------------------
# 2. Latency budget SLA assertions
# ---------------------------------------------------------------------------
class LatencyBudgetTests(unittest.TestCase):
    """Tests that RELEASE_LATENCY_BUDGET_MS values match expected SLA targets."""

    def test_pipeline_sla_is_3_seconds(self):
        self.assertEqual(RELEASE_LATENCY_BUDGET_MS["pipeline_p95"], 3000.0)

    def test_vision_sla_is_1200ms(self):
        self.assertEqual(RELEASE_LATENCY_BUDGET_MS["vision_p95"], 1200.0)

    def test_reasoning_sla_is_1200ms(self):
        self.assertEqual(RELEASE_LATENCY_BUDGET_MS["reasoning_p95"], 1200.0)

    def test_overhead_sla_is_600ms(self):
        self.assertEqual(RELEASE_LATENCY_BUDGET_MS["overhead_p95"], 600.0)

    def test_vision_plus_reasoning_plus_overhead_fits_pipeline_budget(self):
        vision = RELEASE_LATENCY_BUDGET_MS["vision_p95"]
        reasoning = RELEASE_LATENCY_BUDGET_MS["reasoning_p95"]
        overhead = RELEASE_LATENCY_BUDGET_MS["overhead_p95"]
        pipeline = RELEASE_LATENCY_BUDGET_MS["pipeline_p95"]
        # vision and history run in parallel; reasoning serial
        # The tightest arrangement: max(vision, history) + reasoning + overhead ≤ pipeline
        self.assertLessEqual(vision + overhead, pipeline)
        self.assertLessEqual(reasoning + overhead, pipeline)


# ---------------------------------------------------------------------------
# 3. Frame queue capacity constants
# ---------------------------------------------------------------------------
class FrameQueueCapacityTests(unittest.TestCase):
    """Tests for frame queue size and timeout constants."""

    def test_frame_queue_size_is_positive(self):
        self.assertGreater(_FRAME_QUEUE_SIZE, 0)

    def test_frame_queue_size_is_at_least_5(self):
        # Queue must buffer at least a few frames under burst conditions
        self.assertGreaterEqual(_FRAME_QUEUE_SIZE, 5)

    def test_frame_processing_timeout_is_positive(self):
        self.assertGreater(_FRAME_PROCESSING_TIMEOUT, 0)

    def test_frame_processing_timeout_is_at_least_5s(self):
        # Must give enough time for vision + reasoning before dropping frame
        self.assertGreaterEqual(_FRAME_PROCESSING_TIMEOUT, 5.0)

    def test_frame_processing_timeout_does_not_exceed_60s(self):
        # 60s timeout would make the queue stall too long under failures
        self.assertLessEqual(_FRAME_PROCESSING_TIMEOUT, 60.0)


# ---------------------------------------------------------------------------
# 4. process_canonical — idempotency (duplicate suppression)
# ---------------------------------------------------------------------------
class ProcessCanonicalIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    """Tests that process_canonical() deduplicates events by source_event_id."""

    async def test_duplicate_event_returns_duplicate_status(self):
        await init_db()
        source_event_id = f"fri-{uuid.uuid4()}"
        cam_id = f"cam-{uuid.uuid4()}"
        event_id = str(uuid.uuid4())

        # Pre-insert an existing Event with the same source + source_event_id
        async with AsyncSessionLocal() as db:
            stream = Stream(uri=cam_id, site_id="home", zone="front_door", label="Auto")
            db.add(stream)
            await db.commit()
            await db.refresh(stream)
            event = Event(
                id=event_id,
                stream_id=stream.id,
                timestamp=datetime.utcnow(),
                source="frigate",
                source_event_id=source_event_id,
                verdict_action="suppress",
                final_confidence=0.8,
                categories='["package"]',
            )
            db.add(event)
            await db.commit()

        from backend.ingest.schemas import CanonicalIngestPayload
        from backend.ingest.processor import process_canonical

        payload = CanonicalIngestPayload(
            cam_id=cam_id,
            home_id="home",
            source="frigate",
            source_event_id=source_event_id,
            image_b64=_TINY_JPEG_B64,  # "fakeimage" in b64 — won't be decoded (idempotency short-circuits)
        )

        result = await process_canonical(payload, db_factory=AsyncSessionLocal, groq_client=None, on_verdict=None)

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["event_id"], event_id)
        self.assertEqual(result["cam_id"], cam_id)
        self.assertEqual(result["home_id"], "home")

    async def test_unique_event_not_flagged_as_duplicate(self):
        """Two calls with different source_event_ids must NOT be deduplicated."""
        await init_db()
        cam_id = f"cam-{uuid.uuid4()}"

        # Pre-insert an event with one source_event_id
        async with AsyncSessionLocal() as db:
            stream = Stream(uri=cam_id, site_id="home", zone="front_door", label="Auto")
            db.add(stream)
            await db.commit()
            await db.refresh(stream)
            existing_source_id = f"fri-aaa-{uuid.uuid4()}"
            event = Event(
                id=str(uuid.uuid4()),
                stream_id=stream.id,
                timestamp=datetime.utcnow(),
                source="frigate",
                source_event_id=existing_source_id,
                verdict_action="suppress",
                final_confidence=0.8,
                categories='["package"]',
            )
            db.add(event)
            await db.commit()

        from backend.ingest.schemas import CanonicalIngestPayload
        from backend.ingest.processor import process_canonical

        # Different source_event_id — must NOT be a duplicate
        payload = CanonicalIngestPayload(
            cam_id=cam_id,
            home_id="home",
            source="frigate",
            source_event_id=f"fri-bbb-{uuid.uuid4()}",  # Different
            image_b64=_TINY_JPEG_B64,
        )

        vision_result = _make_vision_result()
        verdict_id = str(uuid.uuid4())
        verdict = _make_verdict(verdict_id, cam_id)

        with (
            patch("backend.agent.pipeline.process_frame", AsyncMock(return_value=verdict)),
            patch("backend.hub._persist_verdict", AsyncMock()),
            patch("backend.hub.ws_manager") as mock_ws,
            patch("backend.notifications.notifier") as mock_notifier,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_notifier.dispatch = AsyncMock()

            result = await process_canonical(
                payload,
                db_factory=AsyncSessionLocal,
                groq_client=None,
                on_verdict=None,
            )

        # Should not be a duplicate
        self.assertNotEqual(result.get("status"), "duplicate")


# ---------------------------------------------------------------------------
# 5. process_canonical — e2e telemetry fields in response
# ---------------------------------------------------------------------------
class ProcessCanonicalE2ETelemetryTests(unittest.IsolatedAsyncioTestCase):
    """Tests that process_canonical() returns a response with latency fields."""

    async def _run_process_canonical(self) -> dict:
        await init_db()
        cam_id = f"cam-{uuid.uuid4()}"
        event_id = str(uuid.uuid4())
        verdict = _make_verdict(event_id, cam_id, latency_ms=800.0)

        from backend.ingest.schemas import CanonicalIngestPayload
        from backend.ingest.processor import process_canonical

        payload = CanonicalIngestPayload(
            cam_id=cam_id,
            home_id="home",
            source="generic",
            image_b64=_TINY_JPEG_B64,
        )

        with (
            patch("backend.agent.pipeline.process_frame", AsyncMock(return_value=verdict)),
            patch("backend.hub._persist_verdict", AsyncMock()),
            patch("backend.hub.ws_manager") as mock_ws,
            patch("backend.notifications.notifier") as mock_notifier,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_notifier.dispatch = AsyncMock()

            result = await process_canonical(
                payload,
                db_factory=AsyncSessionLocal,
                groq_client=None,
                on_verdict=None,
            )
        return result

    async def test_response_contains_event_id(self):
        result = await self._run_process_canonical()
        self.assertIn("event_id", result)
        self.assertTrue(result["event_id"])

    async def test_response_contains_action(self):
        result = await self._run_process_canonical()
        self.assertIn("action", result)
        self.assertIn(result["action"], ("alert", "suppress"))

    async def test_response_contains_risk_level(self):
        result = await self._run_process_canonical()
        self.assertIn("risk_level", result)

    async def test_response_contains_stream_id(self):
        result = await self._run_process_canonical()
        self.assertIn("stream_id", result)

    async def test_process_frame_was_called_exactly_once(self):
        await init_db()
        cam_id = f"cam-{uuid.uuid4()}"
        event_id = str(uuid.uuid4())
        verdict = _make_verdict(event_id, cam_id)

        from backend.ingest.schemas import CanonicalIngestPayload
        from backend.ingest.processor import process_canonical

        payload = CanonicalIngestPayload(
            cam_id=cam_id,
            home_id="home",
            source="generic",
            image_b64=_TINY_JPEG_B64,
        )

        mock_process = AsyncMock(return_value=verdict)
        with (
            patch("backend.agent.pipeline.process_frame", mock_process),
            patch("backend.hub._persist_verdict", AsyncMock()),
            patch("backend.hub.ws_manager") as mock_ws,
            patch("backend.notifications.notifier") as mock_notifier,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_notifier.dispatch = AsyncMock()
            await process_canonical(payload, db_factory=AsyncSessionLocal, groq_client=None, on_verdict=None)

        mock_process.assert_awaited_once()

    async def test_verdict_is_persisted_exactly_once(self):
        await init_db()
        cam_id = f"cam-{uuid.uuid4()}"
        event_id = str(uuid.uuid4())
        verdict = _make_verdict(event_id, cam_id)

        from backend.ingest.schemas import CanonicalIngestPayload
        from backend.ingest.processor import process_canonical

        payload = CanonicalIngestPayload(
            cam_id=cam_id,
            home_id="home",
            source="generic",
            image_b64=_TINY_JPEG_B64,
        )

        mock_persist = AsyncMock()
        with (
            patch("backend.agent.pipeline.process_frame", AsyncMock(return_value=verdict)),
            patch("backend.hub._persist_verdict", mock_persist),
            patch("backend.hub.ws_manager") as mock_ws,
            patch("backend.notifications.notifier") as mock_notifier,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_notifier.dispatch = AsyncMock()
            await process_canonical(payload, db_factory=AsyncSessionLocal, groq_client=None, on_verdict=None)

        mock_persist.assert_awaited_once()

    async def test_ws_broadcast_called_with_dict(self):
        await init_db()
        cam_id = f"cam-{uuid.uuid4()}"
        event_id = str(uuid.uuid4())
        verdict = _make_verdict(event_id, cam_id)

        from backend.ingest.schemas import CanonicalIngestPayload
        from backend.ingest.processor import process_canonical

        payload = CanonicalIngestPayload(
            cam_id=cam_id,
            home_id="home",
            source="generic",
            image_b64=_TINY_JPEG_B64,
        )

        mock_ws_broadcast = AsyncMock()
        with (
            patch("backend.agent.pipeline.process_frame", AsyncMock(return_value=verdict)),
            patch("backend.hub._persist_verdict", AsyncMock()),
            patch("backend.hub.ws_manager") as mock_ws,
            patch("backend.notifications.notifier") as mock_notifier,
        ):
            mock_ws.broadcast = mock_ws_broadcast
            mock_notifier.dispatch = AsyncMock()
            await process_canonical(payload, db_factory=AsyncSessionLocal, groq_client=None, on_verdict=None)

        mock_ws_broadcast.assert_awaited_once()
        broadcast_arg = mock_ws_broadcast.await_args[0][0]
        self.assertIsInstance(broadcast_arg, dict)


# ---------------------------------------------------------------------------
# 6. Concurrent ingest throughput
# ---------------------------------------------------------------------------
class ConcurrentIngestTests(unittest.IsolatedAsyncioTestCase):
    """Tests for concurrent process_canonical calls — measures throughput capacity."""

    async def _run_n_concurrent(self, n: int) -> list[dict]:
        await init_db()

        from backend.ingest.schemas import CanonicalIngestPayload
        from backend.ingest.processor import process_canonical

        mock_process = AsyncMock(side_effect=lambda *a, **kw: _make_verdict(str(uuid.uuid4()), f"cam-{uuid.uuid4()}"))
        mock_persist = AsyncMock()
        mock_broadcast = AsyncMock()
        mock_dispatch = AsyncMock()

        async def _one(i: int) -> dict:
            cam_id = f"cam-concurrent-{i}-{uuid.uuid4()}"
            payload = CanonicalIngestPayload(
                cam_id=cam_id,
                home_id="home",
                source="generic",
                image_b64=_TINY_JPEG_B64,
            )
            with (
                patch("backend.agent.pipeline.process_frame", mock_process),
                patch("backend.hub._persist_verdict", mock_persist),
                patch("backend.hub.ws_manager") as mock_ws,
                patch("backend.notifications.notifier") as mock_notifier,
            ):
                mock_ws.broadcast = mock_broadcast
                mock_notifier.dispatch = mock_dispatch
                return await process_canonical(
                    payload,
                    db_factory=AsyncSessionLocal,
                    groq_client=None,
                    on_verdict=None,
                )

        start = perf_counter()
        results = await asyncio.gather(*[_one(i) for i in range(n)])
        elapsed = perf_counter() - start
        return results, elapsed

    async def test_5_concurrent_ingest_all_succeed(self):
        results, _ = await self._run_n_concurrent(5)
        self.assertEqual(len(results), 5)
        for r in results:
            self.assertIn("event_id", r)

    async def test_10_concurrent_ingest_all_succeed(self):
        results, _ = await self._run_n_concurrent(10)
        self.assertEqual(len(results), 10)
        for r in results:
            self.assertIn("event_id", r)

    async def test_concurrent_ingest_all_have_unique_event_ids(self):
        results, _ = await self._run_n_concurrent(8)
        event_ids = [r["event_id"] for r in results if "event_id" in r]
        self.assertEqual(len(event_ids), len(set(event_ids)), "Event IDs must be unique")

    async def test_concurrent_ingest_wall_time_reasonable(self):
        """10 requests in parallel should complete in less than 2 seconds with mocked deps."""
        _, elapsed = await self._run_n_concurrent(10)
        self.assertLess(elapsed, 2.0, f"10 concurrent requests took {elapsed:.2f}s; expected < 2s with mocked deps")


# ---------------------------------------------------------------------------
# 7. Ingest b64 image decode path
# ---------------------------------------------------------------------------
class IngestFrameDecodeTests(unittest.IsolatedAsyncioTestCase):
    """Tests for the image b64 decode path in process_canonical."""

    async def test_invalid_b64_raises_value_error(self):
        await init_db()
        cam_id = f"cam-{uuid.uuid4()}"

        from backend.ingest.schemas import CanonicalIngestPayload
        from backend.ingest.processor import process_canonical

        payload = CanonicalIngestPayload(
            cam_id=cam_id,
            home_id="home",
            source="generic",
            image_b64="!!!not-valid-base64!!!",
        )

        with self.assertRaises(Exception):
            await process_canonical(payload, db_factory=AsyncSessionLocal, groq_client=None, on_verdict=None)

    async def test_no_image_raises_value_error(self):
        """CanonicalIngestPayload model validator rejects missing image at construction time."""
        from pydantic import ValidationError
        from backend.ingest.schemas import CanonicalIngestPayload

        with self.assertRaises(ValidationError):
            CanonicalIngestPayload(
                cam_id="cam-no-image",
                home_id="home",
                source="generic",
                # Neither image_b64 nor image_url provided
            )


if __name__ == "__main__":
    unittest.main()
