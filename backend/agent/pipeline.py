from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any

import numpy as np
from groq import AsyncGroq
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent import history as history_agent
from backend.agent import vision as vision_agent
from backend.agent.bus import AgentMessageBus
from backend.agent.ingest import FrameSource, make_source
from backend.agent.reasoning.arbiter import run_reasoning, _ALERT_THRESHOLD, compute_home_thresholds
from backend.config import settings
from backend.metrics import get_metrics
from backend.models.schemas import EventContext, FramePacket, StreamMeta, Verdict, VisionResult, HistoryContext, MachineRouting
from backend.policy import ENTRY_ZONES, POLICY_VERSION, PROMPT_VERSION, RELEASE_LATENCY_BUDGET_MS

logger = logging.getLogger(__name__)

_REASONING_AGENT_IDS = [
    "context_baseline_reasoner",
    "trajectory_intent_assessor",
    "falsification_auditor",
    "executive_triage_commander",
]

# Frame buffer queue size - prevents memory explosion on slow processing
_FRAME_QUEUE_SIZE = 10
_FRAME_PROCESSING_TIMEOUT = 30.0  # seconds
_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


async def process_frame(
    frame,
    stream_meta: StreamMeta,
    db: AsyncSession,
    groq_client: AsyncGroq,
    event_id: str | None = None,
    event_context: EventContext | None = None,
) -> Verdict:
    total_started = perf_counter()
    frame_id = event_id or str(uuid.uuid4())
    timestamp = datetime.utcnow()

    b64 = vision_agent.encode_frame(frame)

    history_started = perf_counter()
    vision_result, history_ctx = await asyncio.gather(
        vision_agent.analyse_frame(
            b64,
            stream_meta,
            groq_client,
            ingest_metadata=event_context.metadata if event_context else None,
        ),
        history_agent.query_history(
            db=db,
            stream_id=stream_meta.stream_id,
            site_id=stream_meta.site_id,
            event_types=["person", "pet", "package", "vehicle", "intrusion", "motion"],
            source_event_id=event_context.source_event_id if event_context else None,
        ),
    )
    history_elapsed_ms = round((perf_counter() - history_started) * 1000, 2)

    packet = FramePacket(
        frame_id=frame_id,
        stream_id=stream_meta.stream_id,
        timestamp=timestamp,
        b64_frame=b64,
        stream_meta=stream_meta,
        vision=vision_result,
        history=history_ctx,
        event_context=event_context,
    )

    bus = AgentMessageBus(_REASONING_AGENT_IDS)
    reasoning_started = perf_counter()
    verdict = await run_reasoning(
        packet,
        b64_thumbnail=b64,
        bus=bus,
        client=groq_client,
        db=db,
    )
    reasoning_elapsed_ms = round((perf_counter() - reasoning_started) * 1000, 2)
    telemetry_update: dict = {
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "latency_budget_ms": RELEASE_LATENCY_BUDGET_MS,
        "vision_latency_ms": round(float(vision_result.latency_ms), 2),
        "history_latency_ms": history_elapsed_ms,
        "reasoning_latency_ms": reasoning_elapsed_ms,
        "pipeline_latency_ms": round((perf_counter() - total_started) * 1000, 2),
    }
    if getattr(vision_result, "usage", None) and vision_result.usage:
        telemetry_update["vision_prompt_tokens"] = vision_result.usage.get("prompt_tokens", 0)
        telemetry_update["vision_completion_tokens"] = vision_result.usage.get("completion_tokens", 0)
        telemetry_update["vision_total_tokens"] = vision_result.usage.get("total_tokens", 0)
    verdict.telemetry.update(telemetry_update)

    # Apply temporal correlation adjustments
    verdict = await _apply_temporal_correlation(
        db=db,
        verdict=verdict,
        stream_meta=stream_meta,
    )
    verdict.telemetry["pipeline_latency_ms"] = round((perf_counter() - total_started) * 1000, 2)

    # Collect metrics
    metrics = get_metrics()
    metrics.observe_latency("pipeline", verdict.telemetry["pipeline_latency_ms"])
    metrics.observe_latency("vision", verdict.telemetry["vision_latency_ms"])
    metrics.observe_latency("reasoning", verdict.telemetry["reasoning_latency_ms"])
    metrics.increment_request(verdict.routing.action)

    # Log full latency breakdown
    t = verdict.telemetry
    p1 = t.get("reasoning_phase1_latency_ms", 0)
    p2 = t.get("reasoning_phase2_latency_ms", 0)
    logger.info(
        "Pipeline latency breakdown: total=%.0f ms | vision+history=%.0f (parallel) | reasoning=%.0f (phase1=%.0f phase2=%.0f) | overhead=%.0f",
        t.get("pipeline_latency_ms", 0),
        history_elapsed_ms,
        reasoning_elapsed_ms,
        p1,
        p2,
        max(0, t.get("pipeline_latency_ms", 0) - history_elapsed_ms - reasoning_elapsed_ms),
    )
    vision_tok = t.get("vision_total_tokens", 0) or 0
    reasoning_tok = t.get("reasoning_total_tokens", 0) or 0
    if vision_tok or reasoning_tok:
        logger.info(
            "Token usage: vision=%d (in=%d out=%d) reasoning=%d (in=%d out=%d) total=%d",
            vision_tok,
            t.get("vision_prompt_tokens", 0),
            t.get("vision_completion_tokens", 0),
            reasoning_tok,
            t.get("reasoning_prompt_tokens", 0),
            t.get("reasoning_completion_tokens", 0),
            vision_tok + reasoning_tok,
        )

    return verdict


async def _apply_temporal_correlation(
    db: AsyncSession,
    verdict: Verdict,
    stream_meta: StreamMeta,
) -> Verdict:
    """Apply temporal correlation and schedule-based adjustments to verdict.
    
    This adds intelligence to reduce false positives by:
    1. Detecting sequences (delivery, intrusion, resident patterns)
    2. Learning household schedules (quiet hours, peak hours)
    """
    if _should_skip_temporal_correlation(verdict):
        return verdict
    
    from backend.agent.sequence import SequenceDetector, SequenceEvent
    from backend.agent.schedule import ScheduleLearner

    def _current_alert_signal() -> float:
        base = verdict.audit.liability_digest.confidence_score
        if verdict.routing.action == "alert":
            return max(0.0, min(1.0, base))
        return max(0.0, min(1.0, 1.0 - base))

    def _apply_adjustment(delta: float) -> float:
        new_alert_signal = max(0.0, min(1.0, _current_alert_signal() + delta))
        verdict.routing = _rebuild_routing(verdict, stream_meta, new_alert_signal)
        verdict.audit.liability_digest.confidence_score = (
            new_alert_signal if verdict.routing.action == "alert" else 1.0 - new_alert_signal
        )
        return new_alert_signal
    
    adjustments = []
    
    # 1. Sequence analysis
    try:
        sequence_detector = SequenceDetector()
        recent_events = await sequence_detector.get_recent_events(
            db=db,
            stream_id=stream_meta.stream_id,
            window_minutes=15,
        )
        seq_analysis = await sequence_detector.analyze_sequence(
            current_event=SequenceEvent(
                event_id=verdict.event_id,
                stream_id=verdict.stream_id,
                zone=stream_meta.zone,
                timestamp=verdict.timestamp,
                categories=list(verdict.routing.categories),
                source=verdict.event_context.source if verdict.event_context else None,
                source_event_id=verdict.event_context.source_event_id if verdict.event_context else None,
            ),
            recent_events=recent_events,
        )
        if seq_analysis.is_sequenced and seq_analysis.adjustment != 0:
            new_alert_signal = _apply_adjustment(seq_analysis.adjustment)
            verdict.audit.liability_digest.decision_reasoning += f" | Sequence: {seq_analysis.reason}"
            adjustments.append(f"sequence:{seq_analysis.sequence_type}")
            logger.info(
                "Sequence analysis: type=%s, adjustment=%.2f, alert_signal=%.2f",
                seq_analysis.sequence_type, seq_analysis.adjustment, new_alert_signal
            )
            
            # Link detected events to form a sequence chain
            if recent_events:
                try:
                    await sequence_detector.link_events(
                        db=db,
                        events=recent_events,
                        sequence_type=seq_analysis.sequence_type,
                    )
                    logger.info(
                        "Linked %d events into sequence %s",
                        len(recent_events), seq_analysis.sequence_type
                    )
                except Exception as link_err:
                    logger.warning("Failed to link sequence events: %s", link_err)
    except Exception as e:
        logger.warning("Sequence analysis failed: %s", e)
    
    # 2. Schedule analysis
    try:
        schedule_learner = ScheduleLearner()
        schedule_adj = await schedule_learner.get_schedule_adjustment(
            db=db,
            site_id=stream_meta.site_id,
            event_timestamp=verdict.timestamp,
        )
        
        if schedule_adj.adjustment != 0:
            new_alert_signal = _apply_adjustment(schedule_adj.adjustment)
            verdict.audit.liability_digest.decision_reasoning += (
                f" | Schedule: {schedule_adj.reason}"
            )
            adjustments.append(f"schedule:{schedule_adj.is_expected}")
            logger.info(
                "Schedule adjustment: %s, adjustment=%.2f, alert_signal=%.2f",
                schedule_adj.reason, schedule_adj.adjustment, new_alert_signal
            )
    except Exception as e:
        logger.warning("Schedule analysis failed: %s", e)
    
    if adjustments:
        _refresh_verdict_surfaces(verdict, stream_meta)
        logger.info(
            "Temporal correlation applied: %s for event %s",
            ", ".join(adjustments), verdict.event_id
        )
    
    return verdict


def _should_skip_temporal_correlation(verdict: Verdict) -> bool:
    if verdict.routing.visibility_policy != "hidden":
        return False
    if verdict.routing.is_threat or verdict.routing.risk_level not in {"none", "low"}:
        return False
    if any(output.rationale.startswith("Agent fallback:") for output in verdict.audit.agent_outputs):
        return False
    low_risk_categories = {"person", "pet", "package", "vehicle", "clear", "motion"}
    return set(verdict.routing.categories).issubset(low_risk_categories)


def _rebuild_routing(verdict: Verdict, stream_meta: StreamMeta, alert_signal: float) -> MachineRouting:
    zone = (stream_meta.zone or "").lower()
    after_hours = verdict.timestamp.hour < 6 or verdict.timestamp.hour >= 20
    threat = verdict.routing.is_threat
    severity = verdict.routing.severity
    severity_rank = _SEVERITY_RANK.get(severity, 0)
    categories = list(verdict.routing.categories)
    action = "alert" if alert_signal >= _ALERT_THRESHOLD and threat else "suppress"

    if action == "alert":
        risk_level = "high"
    elif threat and severity_rank >= _SEVERITY_RANK["medium"]:
        risk_level = "medium"
    elif threat:
        risk_level = "low"
    elif after_hours and zone in ENTRY_ZONES and "person" in categories:
        risk_level = "low"
    elif set(categories) - {"clear"}:
        risk_level = "low"
    else:
        risk_level = "none"

    return MachineRouting(
        is_threat=threat,
        action=action,
        risk_level=risk_level,
        severity=severity,
        categories=categories,
    )


def _refresh_verdict_surfaces(verdict: Verdict, stream_meta: StreamMeta) -> None:
    from backend.agent.case_engine import build_case_state
    from backend.agent.event_narrator import SecurityEventNarrator

    narrator = SecurityEventNarrator()
    packet = FramePacket(
        frame_id=verdict.frame_id,
        stream_id=verdict.stream_id,
        timestamp=verdict.timestamp,
        b64_frame="",
        stream_meta=stream_meta,
        vision=VisionResult(
            threat=verdict.routing.is_threat,
            severity=verdict.routing.severity,
            categories=verdict.routing.categories,
            description=verdict.description,
            bbox=verdict.bbox,
            confidence=verdict.audit.liability_digest.confidence_score,
            latency_ms=0.0,
        ),
        history=HistoryContext(),
        event_context=verdict.event_context,
    )
    final_confidence = verdict.audit.liability_digest.confidence_score
    verdict.summary.headline = narrator.generate_headline(
        packet=packet,
        risk_level=verdict.routing.risk_level,
        final_confidence=final_confidence,
    )
    verdict.summary.narrative = narrator.generate_narrative(
        packet=packet,
        agent_outputs=verdict.audit.agent_outputs,
        risk_level=verdict.routing.risk_level,
        final_confidence=final_confidence,
    )
    case_state = build_case_state(
        packet=packet,
        agent_outputs=verdict.audit.agent_outputs,
        routing=verdict.routing,
        decision_confidence=final_confidence,
        decision_reasoning=verdict.audit.liability_digest.decision_reasoning,
    )
    verdict.case = case_state
    verdict.case_id = case_state.case_id
    verdict.case_status = case_state.case_status
    verdict.ambiguity_state = case_state.ambiguity_state
    verdict.confidence_band = case_state.confidence_band
    verdict.consumer_summary = case_state.consumer_summary
    verdict.operator_summary = case_state.operator_summary
    verdict.evidence_digest = case_state.evidence_digest
    verdict.recommended_next_action = case_state.recommended_next_action
    verdict.recommended_delivery_targets = case_state.recommended_delivery_targets


class StreamPipeline:
    """Stream pipeline with non-blocking frame buffer for high-throughput processing.
    
    Uses a producer-consumer pattern:
    - Producer: reads frames from source and puts them in buffer queue
    - Consumer: worker coroutine that processes frames and emits verdicts
    
    This prevents slow vision/LLM calls from blocking frame capture.
    """
    
    def __init__(
        self,
        stream_meta: StreamMeta,
        db_factory,
        groq_client: AsyncGroq,
        on_verdict,
    ) -> None:
        self._meta = stream_meta
        self._db_factory = db_factory
        self._groq = groq_client
        self._on_verdict = on_verdict
        self._source: FrameSource | None = None
        self._producer_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None
        self._running = False
        # Frame buffer queue - prevents blocking on slow vision processing
        self._frame_queue: asyncio.Queue[tuple[np.ndarray, str, int] | None] | None = None
        self._sample_every_n = max(1, int(settings.stream_sample_every_n_frames))
        self._frame_counter = 0

    def start(self) -> None:
        self._running = True
        self._frame_queue = asyncio.Queue(maxsize=_FRAME_QUEUE_SIZE)
        # Start consumer first (waits for frames)
        self._consumer_task = asyncio.create_task(
            self._consumer_loop(), 
            name=f"pipeline-consumer-{self._meta.stream_id}"
        )
        # Start producer (feeds frames)
        self._producer_task = asyncio.create_task(
            self._producer_loop(), 
            name=f"pipeline-producer-{self._meta.stream_id}"
        )

    async def stop(self) -> None:
        self._running = False
        # Signal producer to stop by sending None
        if self._frame_queue is not None:
            await self._frame_queue.put((None, None, 0))
        
        if self._producer_task:
            self._producer_task.cancel()
            try:
                await self._producer_task
            except asyncio.CancelledError:
                pass
        
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        
        if self._source:
            await self._source.close()
        
        logger.info("Pipeline stopped for stream %s", self._meta.stream_id)

    async def _producer_loop(self) -> None:
        """Producer: reads frames from source and puts them in buffer queue.
        
        Non-blocking - if queue is full, frames are dropped to prevent memory explosion.
        This is acceptable for home security where recent frames are more relevant than old ones.
        """
        self._source = make_source(self._meta.uri)
        logger.info("Pipeline producer started for stream %s (%s)", self._meta.stream_id, self._meta.uri)
        
        try:
            async for frame in self._source.stream():
                if not self._running:
                    break
                self._frame_counter += 1
                if (self._frame_counter - 1) % self._sample_every_n != 0:
                    continue

                frame_id = str(uuid.uuid4())
                
                # Non-blocking put - if queue full, drop frame
                try:
                    self._frame_queue.put_nowait((frame, frame_id, self._frame_counter))
                except asyncio.QueueFull:
                    # Log dropped frame but don't block
                    logger.warning(
                        "Frame buffer full for stream %s - dropping frame %s", 
                        self._meta.stream_id, frame_id
                    )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Pipeline producer error for %s: %s", self._meta.stream_id, exc)
        finally:
            # Signal consumer to stop
            if self._frame_queue is not None:
                await self._frame_queue.put((None, None, 0))
            logger.info("Pipeline producer stopped for stream %s", self._meta.stream_id)

    async def _consumer_loop(self) -> None:
        """Consumer: processes frames from queue and emits verdicts.
        
        Runs in separate task, so slow vision/LLM calls don't block frame capture.
        """
        logger.info("Pipeline consumer started for stream %s", self._meta.stream_id)
        
        while self._running:
            try:
                # Wait for frame with timeout to allow checking _running
                try:
                    item = await asyncio.wait_for(
                        self._frame_queue.get(), 
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                    
                frame, event_id, frame_index = item
                
                # None signal means producer stopped
                if frame is None:
                    break
                    
                try:
                    async with self._db_factory() as db:
                        event_context = EventContext(
                            source="stream",
                            cam_id=self._meta.stream_id,
                            home_id=self._meta.site_id,
                            zone=self._meta.zone,
                            label=self._meta.label,
                            ingest_mode="stream_sampled",
                            frame_index=frame_index,
                            sampled=True,
                            sample_rate=self._sample_every_n,
                            metadata={"uri": self._meta.uri},
                        )
                        verdict = await asyncio.wait_for(
                            process_frame(
                                frame=frame,
                                stream_meta=self._meta,
                                db=db,
                                groq_client=self._groq,
                                event_id=event_id,
                                event_context=event_context,
                            ),
                            timeout=_FRAME_PROCESSING_TIMEOUT,
                        )
                    await self._on_verdict(verdict, frame)
                except asyncio.TimeoutError:
                    logger.error(
                        "Frame processing timeout for stream %s, event %s", 
                        self._meta.stream_id, event_id
                    )
                except Exception as exc:
                    logger.exception(
                        "Pipeline frame error for %s, event %s: %s", 
                        self._meta.stream_id, event_id, exc
                    )
            except Exception as exc:
                logger.exception("Consumer loop error for %s: %s", self._meta.stream_id, exc)
        
        logger.info("Pipeline consumer stopped for stream %s", self._meta.stream_id)
