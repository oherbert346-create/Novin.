from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.bus import AgentMessageBus
from backend.agent.case_engine import build_case_state
from backend.agent.hallucination_guard import detect_hallucination_markers
from backend.agent.event_narrator import SecurityEventNarrator
from backend.config import settings
from backend.models.db import HomeThresholdConfig
from backend.models.schemas import AgentOutput, FramePacket, Verdict
from backend.policy import (
    BENIGN_CATEGORIES,
    ENTRY_ZONES,
    HARD_BENIGN_RISK_LABELS,
    HARD_THREAT_RISK_LABELS,
    HOME_SECURITY_RISK_HINTS,
    POLICY_VERSION,
    PROMPT_VERSION,
    RELEASE_LATENCY_BUDGET_MS,
)

logger = logging.getLogger(__name__)

# Weights must sum to 1.0 (cognitive pipeline: Agent 4 is final arbiter)
_WEIGHTS = {
    "context_baseline_reasoner": 0.15,
    "trajectory_intent_assessor": 0.20,
    "falsification_auditor": 0.15,
    "executive_triage_commander": 0.50,
}

# Severity gate: only alert if vision severity is at least this level
_SEVERITY_ORDER = ["none", "low", "medium", "high", "critical"]
_RISK_THREAT_VALUES = {"intrusion", "forced_entry", "suspicious_person", "threat", "high_risk", "critical_risk"}
# Alert threshold - from settings with fallback for backward compatibility
_ALERT_THRESHOLD = settings.alert_threshold
_MIN_SEVERITY_TO_ALERT = settings.min_severity_to_alert


def _apply_launch_guardrails(
    *,
    packet: FramePacket,
    agent_outputs: list[AgentOutput],
    risk_level: str,
    action: str,
) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    risk_labels = {label.lower() for label in packet.vision.risk_labels if label}
    categories = {label.lower() for label in packet.vision.categories if label}
    after_hours = packet.timestamp.hour < 6 or packet.timestamp.hour >= 20
    has_explicit_threat = bool(risk_labels & HARD_THREAT_RISK_LABELS)
    has_wildlife_entry = (
        (packet.stream_meta.zone or "").lower() in ENTRY_ZONES
        and "wildlife_near_entry" in risk_labels
    )
    has_explicit_benign = bool(risk_labels & HARD_BENIGN_RISK_LABELS) or categories.issubset(BENIGN_CATEGORIES)
    any_uncertain = any(output.verdict == "uncertain" for output in agent_outputs)
    grounded_alert_support = any(
        output.verdict == "alert"
        and output.confidence >= 0.55
        and output.agent_id not in {"adversarial_challenger", "falsification_auditor"}
        for output in agent_outputs
    )
    wildlife_entry_hazard = has_wildlife_entry and (after_hours or grounded_alert_support)

    if action == "suppress" and has_explicit_threat and not any_uncertain:
        action = "alert"
        risk_level = "high" if {"tamper", "forced_entry"} & risk_labels else "medium"
        notes.append("guardrail: escalated explicit threat cue")

    if action == "suppress" and wildlife_entry_hazard:
        action = "alert"
        if risk_level in {"none", "low"}:
            risk_level = "medium"
        notes.append("guardrail: escalated wildlife entry hazard")

    if action == "alert" and has_explicit_benign and not (has_explicit_threat or wildlife_entry_hazard):
        action = "suppress"
        risk_level = "low" if categories - {"clear"} else "none"
        notes.append("guardrail: suppressed benign-only scene")

    return risk_level, action, notes


async def compute_home_thresholds(
    db: AsyncSession,
    site_id: str,
) -> dict[str, float]:
    """
    Compute adaptive confidence thresholds for a home based on user feedback.
    
    Thresholds are adapted using FP/FN rates over the past 30 days:
    - High FP rate (>20%) → raise vote_confidence_threshold toward 0.75
    - High FN rate (>10%) → lower vote_confidence_threshold toward 0.35
    - Rate limiting prevents oscillation: max ±0.05 per 24h
    
    Args:
        db: AsyncSession for database access
        site_id: Home site identifier
    
    Returns:
        Dict with adaptive thresholds:
        {
            "vote_confidence_threshold": float,
            "strong_vote_threshold": float,
            "min_alert_confidence": float,
        }
    """
    # Query current threshold config for this home
    result = await db.execute(
        select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == site_id)
    )
    config = result.scalar_one_or_none()
    
    if config is None:
        # Fallback: home not yet configured, use defaults
        return {
            "vote_confidence_threshold": 0.55,
            "strong_vote_threshold": 0.70,
            "min_alert_confidence": 0.35,
        }
    
    # Calculate FP/FN rates
    total_alerts = config.total_alerts_30d or 0
    fp_count = config.fp_count_30d or 0
    fn_count = config.fn_count_30d or 0
    
    # Avoid division by zero; need at least 50 alerts for adaptation
    if total_alerts < 50:
        # Not enough data yet, use defaults
        return {
            "vote_confidence_threshold": config.vote_confidence_threshold,
            "strong_vote_threshold": config.strong_vote_threshold,
            "min_alert_confidence": config.min_alert_confidence,
        }
    
    fp_rate = fp_count / total_alerts
    fn_rate = fn_count / total_alerts
    
    # Start with current values
    vote_threshold = config.vote_confidence_threshold
    
    # Check rate limiting: max ±0.05 per 24h
    now = datetime.utcnow()
    last_tuned = config.last_tuned
    can_adjust = True
    max_delta = 0.05
    
    if last_tuned is not None:
        hours_since_tuning = (now - last_tuned).total_seconds() / 3600
        if hours_since_tuning < 24:
            # Within rate limit window; scale max delta proportionally
            max_delta = 0.05 * (hours_since_tuning / 24.0)
            can_adjust = hours_since_tuning >= 1  # At least 1 hour must pass before any adjustment
    
    if not can_adjust:
        # Too soon to adjust
        return {
            "vote_confidence_threshold": vote_threshold,
            "strong_vote_threshold": config.strong_vote_threshold,
            "min_alert_confidence": config.min_alert_confidence,
        }
    
    # Adaptive logic based on FP/FN rates
    if fp_rate > 0.20:
        # High false positive rate: raise confidence threshold to reduce false positives
        # Linear scaling: 20% FP → 0.60, 40% FP → 0.70, 50%+ → 0.75
        target_threshold = min(0.75, 0.55 + (fp_rate - 0.20) * 0.5)
        delta = target_threshold - vote_threshold
        delta = max(-max_delta, min(max_delta, delta))  # Clamp to rate limit
        vote_threshold = vote_threshold + delta
    elif fn_rate > 0.10:
        # High false negative rate: lower confidence threshold to catch more threats
        # Linear scaling: 10% FN → 0.50, 20% FN → 0.40, 30%+ → 0.35
        target_threshold = max(0.35, 0.55 - (fn_rate - 0.10) * 2.0)
        delta = target_threshold - vote_threshold
        delta = max(-max_delta, min(max_delta, delta))  # Clamp to rate limit
        vote_threshold = vote_threshold + delta
    
    # Ensure thresholds stay within valid bounds
    vote_threshold = max(0.0, min(1.0, vote_threshold))
    
    # Strong threshold usually stays above vote threshold
    strong_threshold = max(vote_threshold + 0.05, config.strong_vote_threshold)
    strong_threshold = max(0.0, min(1.0, strong_threshold))
    
    return {
        "vote_confidence_threshold": round(vote_threshold, 3),
        "strong_vote_threshold": round(strong_threshold, 3),
        "min_alert_confidence": config.min_alert_confidence,  # Kept fixed for now
    }


def _severity_index(s: str) -> int:
    try:
        return _SEVERITY_ORDER.index(s)
    except ValueError:
        return 0


def _risk_level_from_severity(severity: str) -> str:
    severity = (severity or "none").lower()
    if severity in {"critical", "high"}:
        return "high"
    if severity == "medium":
        return "medium"
    if severity == "low":
        return "low"
    return "none"


def _routing_policies(risk_level: str) -> tuple[str, str, str, str]:
    if risk_level == "high":
        return "alert", "prominent", "immediate", "full"
    if risk_level == "medium":
        return "suppress", "prominent", "review", "full"
    if risk_level == "low":
        return "suppress", "timeline", "none", "timeline"
    return "suppress", "hidden", "none", "diagnostic"


def _compute_verdict(
    packet: FramePacket,
    agent_outputs: list[AgentOutput],
    b64_thumbnail: str,
    adaptive_thresholds: dict[str, float] | None = None,
) -> Verdict:
    # Extract adaptive thresholds
    if adaptive_thresholds is None:
        adaptive_thresholds = {
            "vote_confidence_threshold": 0.55,
            "strong_vote_threshold": 0.70,
            "min_alert_confidence": 0.35,
        }
    
    vote_threshold = adaptive_thresholds.get("vote_confidence_threshold", 0.55)
    strong_threshold = adaptive_thresholds.get("strong_vote_threshold", 0.70)
    
    risk_labels = [label.lower() for label in packet.vision.risk_labels if label]
    zone = (packet.stream_meta.zone or "").lower()
    entry_zone = zone
    after_hours = packet.timestamp.hour < 6 or packet.timestamp.hour >= 20
    identity_labels = {label.lower() for label in packet.vision.identity_labels if label}
    category_labels = {label.lower() for label in packet.vision.categories if label}
    has_threat_semantic = (
        packet.vision.threat
        or any(label in _RISK_THREAT_VALUES for label in risk_labels)
        or (
            entry_zone in ENTRY_ZONES
            and any(label in {"wildlife_near_entry", "entry_approach"} for label in risk_labels)
        )
    )
    home_security_signal = any(label in HOME_SECURITY_RISK_HINTS for label in risk_labels)

    alert_score = 0.0
    suppress_score = 0.0
    uncertain_score = 0.0
    contributions: list[str] = []
    alert_vote_weight = 0.0
    suppress_vote_weight = 0.0

    for output in agent_outputs:
        weight = _WEIGHTS.get(output.agent_id, 0.0)
        weighted = weight * output.confidence
        contributions.append(
            f"{output.agent_id}:{output.verdict} weighted={weighted:.3f} (w={weight:.2f}, c={output.confidence:.2f})"
        )
        if output.verdict == "alert":
            alert_score += weighted
            alert_vote_weight += weight
        elif output.verdict == "suppress":
            suppress_score += weighted
            suppress_vote_weight += weight
        else:
            uncertain_score += weighted

    total = alert_score + suppress_score
    alert_confidence = alert_score / total if total > 0 else 0.0
    suppress_confidence = suppress_score / total if total > 0 else 0.0

    severity_ok = (
        _severity_index(packet.vision.severity) >= _severity_index(_MIN_SEVERITY_TO_ALERT)
        or (entry_zone in ENTRY_ZONES and (has_threat_semantic or home_security_signal))
    )
    entry_risk_signal = entry_zone in ENTRY_ZONES and (home_security_signal or has_threat_semantic or after_hours)
    _SKEPTIC_AGENTS = {"adversarial_challenger", "falsification_auditor"}
    clear_alert_support = any(
        output.verdict == "alert"
        and output.confidence >= strong_threshold
        and output.agent_id not in _SKEPTIC_AGENTS
        for output in agent_outputs
    )
    medium_alert_support = any(
        output.verdict == "alert"
        and output.confidence >= vote_threshold
        and output.agent_id not in _SKEPTIC_AGENTS
        for output in agent_outputs
    )
    strong_suppress_support = sum(
        1
        for output in agent_outputs
        if output.verdict == "suppress" and output.confidence >= 0.75
    )
    strong_suppress_limit = 2 if has_threat_semantic else 1
    fast_path_alert = (
        (has_threat_semantic or home_security_signal)
        and severity_ok
        and clear_alert_support
        and strong_suppress_support <= strong_suppress_limit
        and (entry_zone in ENTRY_ZONES or after_hours)
    )
    should_alert = (alert_confidence >= _ALERT_THRESHOLD and severity_ok and has_threat_semantic) or fast_path_alert
    base_risk_level = _risk_level_from_severity(packet.vision.severity)
    risk_level = base_risk_level
    if should_alert:
        risk_level = "high"
    elif entry_risk_signal and medium_alert_support:
        risk_level = "medium"
    elif entry_risk_signal and risk_level == "none":
        risk_level = "medium"
    elif has_threat_semantic and risk_level == "none":
        risk_level = "low"
    elif (
        risk_level == "none"
        and category_labels
        and category_labels != {"clear"}
        and not (identity_labels & {"wildlife", "pet", "animal"} and zone not in ENTRY_ZONES)
    ):
        risk_level = "low"

    if any(label in {"tamper", "forced_entry"} for label in risk_labels):
        risk_level = "high" if clear_alert_support else max(risk_level, "medium", key=lambda s: ["none","low","medium","high"].index(s))

    if after_hours and entry_zone in ENTRY_ZONES and any(label in {"entry_approach", "entry_dwell", "suspicious_presence"} for label in risk_labels):
        risk_level = "high" if clear_alert_support else max(risk_level, "medium", key=lambda s: ["none","low","medium","high"].index(s))

    action, visibility_policy, notification_policy, storage_policy = _routing_policies(risk_level)
    risk_level, action, guardrail_notes = _apply_launch_guardrails(
        packet=packet,
        agent_outputs=agent_outputs,
        risk_level=risk_level,
        action=action,
    )
    if action == "alert":
        _, visibility_policy, notification_policy, storage_policy = _routing_policies("high")
    else:
        action, visibility_policy, notification_policy, storage_policy = _routing_policies(risk_level)
    decision_confidence = alert_confidence if action == "alert" else suppress_confidence
    contradiction_checks: list[str] = []
    if not has_threat_semantic and alert_vote_weight > suppress_vote_weight:
        contradiction_checks.append("warn: alert-leaning votes conflict with non-threat vision semantics")
    if has_threat_semantic and suppress_vote_weight > alert_vote_weight:
        contradiction_checks.append("warn: suppress-leaning votes conflict with threat-oriented vision semantics")
    if not contradiction_checks:
        contradiction_checks.append("pass: vision semantics and vote direction are aligned")

    alert_criteria: list[str] = []
    if alert_confidence >= _ALERT_THRESHOLD:
        alert_criteria.append(f"confidence_met {alert_confidence:.0%}>={_ALERT_THRESHOLD:.0%}")
    else:
        alert_criteria.append(f"confidence_unmet {alert_confidence:.0%}<{_ALERT_THRESHOLD:.0%}")
    if severity_ok:
        alert_criteria.append(f"severity_met {packet.vision.severity}>={_MIN_SEVERITY_TO_ALERT}")
    else:
        alert_criteria.append(f"severity_unmet {packet.vision.severity}<{_MIN_SEVERITY_TO_ALERT}")
    if has_threat_semantic:
        alert_criteria.append("threat_semantics_met")
    else:
        alert_criteria.append("threat_semantics_unmet")
    if fast_path_alert:
        alert_criteria.append("fast_path_entry_risk")
    alert_criteria.extend(guardrail_notes)
    alert_criteria.append(f"risk_level={risk_level}")
    alert_criteria.append(f"visibility={visibility_policy}")
    alert_criteria.append(f"notify={notification_policy}")

    executive_output = next((output for output in agent_outputs if output.agent_id == "executive_triage_commander"), None)

    # Generate operator-facing summary text
    narrator = SecurityEventNarrator()
    summary = (
        executive_output.consumer_headline
        if executive_output and executive_output.consumer_headline
        else narrator.generate_headline(
            packet=packet,
            risk_level=risk_level,
            final_confidence=decision_confidence,
        )
    )
    narrative_summary = (
        f"{executive_output.operator_observed} {executive_output.operator_triage}".strip()
        if executive_output and (executive_output.operator_observed or executive_output.operator_triage)
        else narrator.generate_narrative(
            packet=packet,
            agent_outputs=agent_outputs,
            risk_level=risk_level,
            final_confidence=decision_confidence,
        )
    )
    
    decisive_total = alert_score + suppress_score + uncertain_score
    residual = max(0.0, 1.0 - decisive_total)
    decomposition = (
        "CONFIDENCE_DECOMPOSITION: "
        f"selected={decision_confidence:.0%}; "
        f"alert_norm={alert_confidence:.0%}; "
        f"suppress_norm={suppress_confidence:.0%}; "
        f"raw_alert={alert_score:.3f}; "
        f"raw_suppress={suppress_score:.3f}; "
        f"raw_uncertain={uncertain_score:.3f}; "
        f"residual={residual:.3f}; "
        f"contributions=[{'; '.join(contributions)}]."
    )
    checks = f"CONSISTENCY_CHECKS: {' | '.join(contradiction_checks)}."
    alert_basis = "ALERT_BASIS: " + "; ".join(alert_criteria) + "."
    suppress_basis = (
        "SUPPRESS_BASIS: "
        f"final_action={action}; "
        f"risk_level={risk_level}; "
        f"visibility={visibility_policy}; "
        f"notify={notification_policy}; "
        f"suppress_norm={suppress_confidence:.0%}; "
        f"alert_norm={alert_confidence:.0%}."
    )
    decision_reason = (
        "RISK_BASIS: home-security routing assessed this scene as "
        f"{risk_level}. "
        + alert_basis
        + " "
        + suppress_basis
        + " "
        + decomposition
        + " "
        + checks
    )

    from backend.models.schemas import MachineRouting, OperatorSummary, LiabilityDigest, AuditTrail
    
    routing = MachineRouting(
        is_threat=packet.vision.threat,
        action=action,
        risk_level=risk_level,
        severity=packet.vision.severity,
        categories=packet.vision.categories,
        visibility_policy=visibility_policy,
        notification_policy=notification_policy,
        storage_policy=storage_policy,
    )
    
    summary_obj = OperatorSummary(
        headline=summary,
        narrative=narrative_summary,
    )

    liability = LiabilityDigest(
        decision_reasoning=decision_reason,
        confidence_score=round(decision_confidence, 4),
    )
    
    audit = AuditTrail(
        liability_digest=liability,
        agent_outputs=agent_outputs,
    )
    case_state = build_case_state(
        packet=packet,
        agent_outputs=agent_outputs,
        routing=routing,
        decision_confidence=round(decision_confidence, 4),
        decision_reasoning=decision_reason,
    )

    return Verdict(
        frame_id=packet.frame_id,
        event_id=packet.frame_id,
        stream_id=packet.stream_id,
        site_id=packet.stream_meta.site_id,
        timestamp=packet.timestamp,
        routing=routing,
        summary=summary_obj,
        audit=audit,
        description=packet.vision.description,
        bbox=packet.vision.bbox,
        b64_thumbnail=b64_thumbnail,
        event_context=packet.event_context,
        case=case_state,
        case_id=case_state.case_id,
        case_status=case_state.case_status,
        ambiguity_state=case_state.ambiguity_state,
        confidence_band=case_state.confidence_band,
        consumer_summary=case_state.consumer_summary,
        operator_summary=case_state.operator_summary,
        evidence_digest=case_state.evidence_digest,
        recommended_next_action=case_state.recommended_next_action,
        recommended_delivery_targets=case_state.recommended_delivery_targets,
        perception=case_state.perception,
        judgement=case_state.judgement,
        routing_decision=case_state.routing_decision,
        action_readiness=case_state.action_readiness,
    )


async def run_reasoning(
    packet: FramePacket,
    b64_thumbnail: str,
    bus: AgentMessageBus,
    client,
    db: AsyncSession | None = None,
) -> Verdict:
    from backend.agent.reasoning.context_baseline_reasoner import ContextBaselineReasonerAgent
    from backend.agent.reasoning.executive_triage_commander import ExecutiveTriageCommanderAgent
    from backend.agent.reasoning.falsification_auditor import FalsificationAuditorAgent
    from backend.agent.reasoning.trajectory_intent_assessor import TrajectoryIntentAssessorAgent

    agent1 = ContextBaselineReasonerAgent()
    agent2 = TrajectoryIntentAssessorAgent()
    agent3 = FalsificationAuditorAgent()
    agent4 = ExecutiveTriageCommanderAgent()
    started = perf_counter()

    # Phase 1: Parallel strike — specialists see only vision/history
    # All 3 Groq API calls fire concurrently; phase1_latency ≈ max(agent1, agent2, agent3)
    phase1_start = perf_counter()
    logger.info(
        "Phase1: firing 3 reasoning agents in parallel (context_baseline, trajectory_intent, falsification_auditor)"
    )
    (out1, stats1), (out2, stats2), (out3, stats3) = await asyncio.gather(
        _run_agent(agent1, packet, {}, client),
        _run_agent(agent2, packet, {}, client),
        _run_agent(agent3, packet, {}, client),
    )
    phase1_latency_ms = round((perf_counter() - phase1_start) * 1000, 2)
    logger.info(
        "Phase1: all 3 agents finished in %.0f ms (parallel) | agent latencies: %.0f, %.0f, %.0f ms",
        phase1_latency_ms,
        stats1.get("latency_ms", 0),
        stats2.get("latency_ms", 0),
        stats3.get("latency_ms", 0),
    )
    peer = {agent1.agent_id: out1, agent2.agent_id: out2, agent3.agent_id: out3}

    # Phase 2: Triage synthesis — Agent 4 reads all three reports
    phase2_start = perf_counter()
    logger.info("Phase2: executive_triage_commander starting (sequential after Phase1)")
    out4, stats4 = await _run_agent(agent4, packet, peer, client)
    phase2_latency_ms = round((perf_counter() - phase2_start) * 1000, 2)
    logger.info(
        "Phase2: executive_triage_commander finished in %.0f ms | total reasoning %.0f ms",
        phase2_latency_ms,
        phase1_latency_ms + phase2_latency_ms,
    )

    agent_outputs: list[AgentOutput] = [out1, out2, out3, out4]
    per_agent_stats: list[dict[str, Any]] = []
    reasoning_usage: dict[str, int] = {}
    for agent, output, stats in [
        (agent1, out1, stats1),
        (agent2, out2, stats2),
        (agent3, out3, stats3),
        (agent4, out4, stats4),
    ]:
        await bus.publish(agent.agent_id, output)
        agent_usage = stats.get("usage") or {}
        for k, v in agent_usage.items():
            reasoning_usage[k] = reasoning_usage.get(k, 0) + int(v)
        per_agent_stats.append(
            {
                "agent_id": agent.agent_id,
                "round_1_verdict": output.verdict,
                "latency_ms": round(float(stats.get("latency_ms", 0.0)), 2),
                "repair_count": int(stats.get("repair_count", 0)),
                "local_repair_count": int(stats.get("local_repair_count", 0)),
                "invalid_output_count": int(stats.get("invalid_output_count", 0)),
                "model_calls": int(stats.get("model_calls", 0)),
                "skipped": False,
                "fallback": bool(stats.get("fallback", False)),
                "usage": agent_usage,
            }
        )
    await bus.wait_for_all()

    # Compute adaptive thresholds based on home feedback history
    adaptive_thresholds = None
    if db is not None:
        adaptive_thresholds = await compute_home_thresholds(db, packet.stream_meta.site_id)
    
    verdict = _compute_verdict(packet, agent_outputs, b64_thumbnail, adaptive_thresholds)
    text_to_scan = " ".join(
        [o.rationale for o in agent_outputs]
        + [verdict.description or ""]
        + [verdict.audit.liability_digest.decision_reasoning or ""]
    )
    telemetry = {
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "hallucination_markers": detect_hallucination_markers(text_to_scan),
        "latency_budget_ms": RELEASE_LATENCY_BUDGET_MS,
        "reasoning_latency_ms": round((perf_counter() - started) * 1000, 2),
        "reasoning_agent_calls": sum(item["model_calls"] for item in per_agent_stats),
        "reasoning_repairs": sum(item["repair_count"] for item in per_agent_stats),
        "reasoning_local_repairs": sum(item["local_repair_count"] for item in per_agent_stats),
        "reasoning_invalid_output_count": sum(item["invalid_output_count"] for item in per_agent_stats),
        "reasoning_invalid_output_agents": sum(1 for item in per_agent_stats if item["invalid_output_count"] > 0),
        "reasoning_fallback_agents": sum(1 for item in per_agent_stats if item["fallback"]),
        "reasoning_skipped_agents": sum(1 for item in per_agent_stats if item["skipped"]),
        "reasoning_rounds": 2,
        "reasoning_phase1_latency_ms": phase1_latency_ms,
        "reasoning_phase2_latency_ms": phase2_latency_ms,
        "reasoning_agent_stats": per_agent_stats,
        "case_status": verdict.case_status,
        "ambiguity_state": verdict.ambiguity_state,
    }
    if reasoning_usage:
        telemetry["reasoning_prompt_tokens"] = reasoning_usage.get("prompt_tokens", 0)
        telemetry["reasoning_completion_tokens"] = reasoning_usage.get("completion_tokens", 0)
        telemetry["reasoning_total_tokens"] = reasoning_usage.get("total_tokens", 0)
    verdict.telemetry.update(telemetry)
    logger.info(
        "Verdict: %s | confidence=%.2f | frame=%s | reasoning_calls=%d skipped=%d",
        verdict.routing.action,
        verdict.audit.liability_digest.confidence_score,
        packet.frame_id,
        verdict.telemetry["reasoning_agent_calls"],
        verdict.telemetry["reasoning_skipped_agents"],
    )
    return verdict


async def _run_agent(
    agent,
    packet: FramePacket,
    peer_outputs: dict[str, Any],
    client,
) -> tuple[AgentOutput, dict[str, Any]]:
    t0 = perf_counter()
    logger.debug("Reasoning agent %s: API call starting", agent.agent_id)
    try:
        out, stats = await agent.reason_with_metrics(packet, client, peer_outputs=peer_outputs)
        elapsed_ms = round((perf_counter() - t0) * 1000, 0)
        logger.debug(
            "Reasoning agent %s: API call finished in %.0f ms (model_calls=%d)",
            agent.agent_id,
            elapsed_ms,
            stats.get("model_calls", 0),
        )
        return out, stats
    except Exception as exc:
        logger.error("Reasoning phase failed for %s: %s", agent.agent_id, exc)
        return _fallback_output(agent, f"reason_failed: {exc}"), {
            "latency_ms": 0.0,
            "repair_count": 0,
            "local_repair_count": 0,
            "invalid_output_count": 0,
            "model_calls": 0,
            "skipped": False,
            "fallback": True,
        }

def _fallback_output(agent, reason: str) -> AgentOutput:
    return AgentOutput(
        agent_id=agent.agent_id,
        role=agent.role,
        verdict="uncertain",
        risk_level="low",
        confidence=0.0,
        rationale=f"Agent fallback: {reason}"[:280],
        recommended_action="continue monitoring and review next event",
        chain_notes=getattr(agent, "chain_defaults", {}),
    )
