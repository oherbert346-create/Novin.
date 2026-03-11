from __future__ import annotations

from datetime import datetime
from typing import Sequence

from backend.agent.ontology import AMBIGUITY_PATTERNS, BENIGN_PATTERNS, THREAT_PATTERNS, sort_patterns
from backend.models.schemas import (
    AgentOutput,
    ActionIntent,
    ActionReadiness,
    CaseState,
    ConsumerSummary,
    EvidenceItem,
    ExplanationContract,
    FramePacket,
    JudgementDecision,
    MachineRouting,
    Observation,
    OperatorSurfaceSummary,
    PerceptionEvidence,
    RoutingDecision,
)

_ENTRY_ZONES = {"front_door", "porch", "garage", "back_door", "backyard", "living_room", "kitchen"}
_INTERIOR_ZONES = {"living_room", "kitchen", "bedroom", "bathroom", "hallway", "office"}
_THREAT_RISK_LABELS = {
    "forced_entry",
    "entry_dwell",
    "perimeter_progression",
    "tamper",
    "suspicious_presence",
    "suspicious_person",
    "wildlife_near_entry",
}
_BENIGN_RISK_LABELS = {"delivery_pattern", "resident_routine", "benign_activity"}


def build_case_state(
    *,
    packet: FramePacket,
    agent_outputs: Sequence[AgentOutput],
    routing: MachineRouting,
    decision_confidence: float,
    decision_reasoning: str,
) -> CaseState:
    after_hours = packet.timestamp.hour < 6 or packet.timestamp.hour >= 20
    observation = Observation(
        event_id=packet.frame_id,
        stream_id=packet.stream_id,
        observed_at=packet.timestamp,
        zone=packet.stream_meta.zone,
        source=packet.event_context.source if packet.event_context else "stream",
        description=packet.vision.description,
        categories=list(packet.vision.categories),
        identity_labels=list(packet.vision.identity_labels),
        risk_labels=list(packet.vision.risk_labels),
        uncertainty=packet.vision.uncertainty,
        after_hours=after_hours,
        anomaly_score=packet.history.anomaly_score,
        recent_activity_count=len(packet.history.recent_events),
    )

    threat_patterns, benign_patterns, ambiguity_patterns = _classify_patterns(packet, routing, agent_outputs)
    ambiguity_state = _ambiguity_state(packet, agent_outputs, ambiguity_patterns)
    confidence_band = _confidence_band(decision_confidence)
    case_status = _case_status(packet, routing, ambiguity_state, threat_patterns, benign_patterns)
    case_id = _case_id(packet)
    timeline_context = _timeline_context(packet, case_id)
    recommended_next_action = _recommended_next_action(case_status)
    recommended_delivery_targets = _recommended_delivery_targets(case_status, ambiguity_state)

    evidence_digest = _evidence_digest(
        packet=packet,
        routing=routing,
        decision_reasoning=decision_reasoning,
        agent_outputs=agent_outputs,
        threat_patterns=threat_patterns,
        benign_patterns=benign_patterns,
        ambiguity_patterns=ambiguity_patterns,
        timeline_context=timeline_context,
    )

    consumer_summary = _consumer_summary(
        packet=packet,
        agent_outputs=agent_outputs,
        case_status=case_status,
        confidence_band=confidence_band,
        threat_patterns=threat_patterns,
        benign_patterns=benign_patterns,
        recommended_next_action=recommended_next_action,
    )
    operator_summary = _operator_summary(
        packet=packet,
        agent_outputs=agent_outputs,
        routing=routing,
        threat_patterns=threat_patterns,
        benign_patterns=benign_patterns,
        ambiguity_patterns=ambiguity_patterns,
        timeline_context=timeline_context,
        recommended_next_action=recommended_next_action,
    )
    perception = _perception_contract(packet)
    explanation = _explanation_contract(
        packet=packet,
        routing=routing,
        threat_patterns=threat_patterns,
        benign_patterns=benign_patterns,
        ambiguity_patterns=ambiguity_patterns,
        timeline_context=timeline_context,
        decision_reasoning=decision_reasoning,
    )
    judgement = _judgement_contract(
        packet=packet,
        routing=routing,
        decision_confidence=decision_confidence,
        decision_reasoning=decision_reasoning,
        explanation=explanation,
        agent_outputs=agent_outputs,
    )
    routing_decision = _routing_contract(
        routing=routing,
        recommended_delivery_targets=recommended_delivery_targets,
        consumer_summary=consumer_summary,
        operator_summary=operator_summary,
        threat_patterns=threat_patterns,
        benign_patterns=benign_patterns,
        ambiguity_patterns=ambiguity_patterns,
    )
    action_readiness = _action_readiness_contract(
        routing=routing,
        recommended_delivery_targets=recommended_delivery_targets,
        decision_confidence=decision_confidence,
        ambiguity_state=ambiguity_state,
        threat_patterns=threat_patterns,
    )

    return CaseState(
        case_id=case_id,
        case_status=case_status,
        ambiguity_state=ambiguity_state,
        confidence_band=confidence_band,
        observation=observation,
        evidence_digest=evidence_digest,
        consumer_summary=consumer_summary,
        operator_summary=operator_summary,
        recommended_next_action=recommended_next_action,
        recommended_delivery_targets=recommended_delivery_targets,
        threat_patterns=threat_patterns,
        benign_patterns=benign_patterns,
        ambiguity_patterns=ambiguity_patterns,
        perception=perception,
        judgement=judgement,
        routing_decision=routing_decision,
        action_readiness=action_readiness,
    )


def _executive_output(agent_outputs: Sequence[AgentOutput]) -> AgentOutput | None:
    for output in agent_outputs:
        if output.agent_id == "executive_triage_commander":
            return output
    return None


def _case_id(packet: FramePacket) -> str:
    if packet.event_context and isinstance(packet.event_context.metadata, dict):
        scenario_id = str(packet.event_context.metadata.get("scenario_id", "")).strip()
        if scenario_id:
            return scenario_id
        existing = str(packet.event_context.metadata.get("case_id", "")).strip()
        if existing:
            return existing

    for event in packet.history.recent_events + packet.history.similar_events:
        event_context = event.event_context if isinstance(event.event_context, dict) else {}
        metadata = event_context.get("metadata", {}) if isinstance(event_context, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        inherited = str(metadata.get("case_id", "")).strip()
        if inherited:
            return inherited

    if packet.event_context and packet.event_context.source_event_id:
        return f"case-{packet.event_context.source_event_id}"
    return f"case-{packet.frame_id}"


def _classify_patterns(
    packet: FramePacket,
    routing: MachineRouting,
    agent_outputs: Sequence[AgentOutput],
) -> tuple[list[str], list[str], list[str]]:
    categories = {label.lower() for label in packet.vision.categories if label}
    identity_labels = {label.lower() for label in packet.vision.identity_labels if label}
    risk_labels = {label.lower() for label in packet.vision.risk_labels if label}
    zone = (packet.stream_meta.zone or "").lower()
    after_hours = packet.timestamp.hour < 6 or packet.timestamp.hour >= 20
    threat_patterns: set[str] = set()
    benign_patterns: set[str] = set()
    ambiguity_patterns: set[str] = set()

    if "forced_entry" in risk_labels:
        threat_patterns.add("forced_entry")
    if "tamper" in risk_labels:
        threat_patterns.add("tamper")
    if "entry_dwell" in risk_labels:
        threat_patterns.add("entry_dwell")
    if "perimeter_progression" in risk_labels or (
        zone in _ENTRY_ZONES and "person" in categories and after_hours and packet.vision.threat
    ):
        threat_patterns.add("perimeter_progression")
    if zone in _INTERIOR_ZONES and routing.risk_level in {"medium", "high"}:
        threat_patterns.add("interior_breach")
    if "vehicle" in categories and any(label in {"suspicious_presence", "suspicious_person"} for label in risk_labels):
        threat_patterns.add("suspicious_vehicle_behavior")
    if packet.history.recent_events and "person" in categories and after_hours:
        threat_patterns.add("stalking_repeat_presence")
    if "wildlife" in identity_labels or "wildlife_near_entry" in risk_labels:
        threat_patterns.add("dangerous_wildlife")
    if zone in _INTERIOR_ZONES and packet.history.recent_events and packet.history.anomaly_score >= 0.75:
        threat_patterns.add("occupancy_anomaly")
    if any("loiter" in str(output.chain_notes).lower() for output in agent_outputs):
        threat_patterns.add("loitering")

    if "package" in categories or "delivery_pattern" in risk_labels:
        benign_patterns.add("package_delivery")
    if "pet" in categories:
        benign_patterns.add("pet_activity")
    if "vehicle" in categories and not threat_patterns:
        benign_patterns.add("routine_vehicle")
    if "motion" in categories and categories == {"motion"}:
        benign_patterns.add("environmental_motion")
    if packet.history.recent_events and "person" in categories and not packet.vision.threat and not after_hours:
        benign_patterns.add("resident_routine")
    if any(label in _BENIGN_RISK_LABELS for label in risk_labels):
        benign_patterns.add("expected_visitor")
    if packet.history.similar_events and "person" in categories and not packet.vision.threat:
        benign_patterns.add("neighbor_pass_through")

    if packet.vision.uncertainty >= 0.65:
        ambiguity_patterns.add("poor_visibility")
    if packet.vision.uncertainty >= 0.40:
        ambiguity_patterns.add("partial_subject")
    if "motion" in categories and len(categories) == 1:
        ambiguity_patterns.add("isolated_motion")
    if not packet.history.recent_events and not packet.history.similar_events:
        ambiguity_patterns.add("missing_historical_context")

    alert_votes = sum(1 for output in agent_outputs if output.verdict == "alert" and output.confidence >= 0.55)
    suppress_votes = sum(1 for output in agent_outputs if output.verdict == "suppress" and output.confidence >= 0.55)
    if alert_votes and suppress_votes:
        ambiguity_patterns.add("conflicting_evidence")
    if packet.vision.description and any(term in packet.vision.description.lower() for term in {"partial", "occluded", "obscured"}):
        ambiguity_patterns.add("occlusion")

    return (
        sort_patterns(threat_patterns, THREAT_PATTERNS),
        sort_patterns(benign_patterns, BENIGN_PATTERNS),
        sort_patterns(ambiguity_patterns, AMBIGUITY_PATTERNS),
    )


def _ambiguity_state(
    packet: FramePacket,
    agent_outputs: Sequence[AgentOutput],
    ambiguity_patterns: list[str],
) -> str:
    strong_alert = sum(1 for output in agent_outputs if output.verdict == "alert" and output.confidence >= 0.70)
    strong_suppress = sum(1 for output in agent_outputs if output.verdict == "suppress" and output.confidence >= 0.70)
    if "conflicting_evidence" in ambiguity_patterns or (strong_alert and strong_suppress):
        return "contested"
    if packet.vision.uncertainty >= 0.45 or any(output.verdict == "uncertain" for output in agent_outputs):
        return "ambiguous"
    if packet.history.recent_events:
        return "monitoring"
    return "resolved"


def _confidence_band(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"


def _case_status(
    packet: FramePacket,
    routing: MachineRouting,
    ambiguity_state: str,
    threat_patterns: list[str],
    benign_patterns: list[str],
) -> str:
    if routing.action == "alert":
        if any(pattern in {"forced_entry", "tamper", "interior_breach"} for pattern in threat_patterns):
            return "active_threat"
        if routing.risk_level == "high":
            return "urgent"
        if routing.risk_level == "medium":
            return "verify"
        return "watch"

    if routing.risk_level == "medium":
        return "verify"
    if routing.risk_level == "low":
        return "interesting" if ambiguity_state in {"ambiguous", "contested"} else "watch"
    if ambiguity_state in {"ambiguous", "contested"}:
        return "interesting"
    if benign_patterns:
        return "closed_benign"
    if packet.history.recent_events:
        return "watch"
    return "routine"


def _timeline_context(packet: FramePacket, case_id: str) -> str:
    timeline_bits: list[str] = []
    recent = packet.history.recent_events[:2]
    for event in recent:
        delta_minutes = max(0, int((packet.timestamp - event.timestamp).total_seconds() // 60))
        event_context = event.event_context if isinstance(event.event_context, dict) else {}
        metadata = event_context.get("metadata", {}) if isinstance(event_context, dict) else {}
        previous_case = metadata.get("case_id") if isinstance(metadata, dict) else None
        prefix = "same case" if previous_case == case_id else "recent event"
        timeline_bits.append(f"{prefix} {delta_minutes}m ago on {event.stream_id}")
    if packet.history.similar_events and not timeline_bits:
        timeline_bits.append(f"{len(packet.history.similar_events)} similar site events in the last 24h")
    if not timeline_bits:
        timeline_bits.append("no linked case history yet")
    return "; ".join(timeline_bits)


def _recommended_next_action(case_status: str) -> str:
    if case_status == "active_threat":
        return "Escalate immediately, preserve the incident timeline, and notify downstream monitoring."
    if case_status == "urgent":
        return "Send an immediate alert and queue this case for operator review."
    if case_status == "verify":
        return "Notify the homeowner, keep the case visible, and request quick verification."
    if case_status == "watch":
        return "Keep the case open and continue monitoring for recurrence or progression."
    if case_status == "interesting":
        return "Keep visible in the timeline and wait for corroborating evidence before escalation."
    if case_status == "closed_benign":
        return "Keep in the timeline only and close as benign unless new evidence appears."
    return "Store in the timeline only."


def _recommended_delivery_targets(case_status: str, ambiguity_state: str) -> list[str]:
    if case_status == "active_threat":
        return ["homeowner_app", "operator_queue", "webhook", "monitoring"]
    if case_status == "urgent":
        return ["homeowner_app", "operator_queue", "webhook", "timeline"]
    if case_status == "verify":
        return ["homeowner_app", "operator_queue", "timeline"]
    if case_status in {"watch", "interesting"}:
        targets = ["timeline"]
        if ambiguity_state in {"ambiguous", "contested"}:
            targets.append("operator_queue")
        return targets
    return ["timeline"]


def _evidence_digest(
    *,
    packet: FramePacket,
    routing: MachineRouting,
    decision_reasoning: str,
    agent_outputs: Sequence[AgentOutput],
    threat_patterns: list[str],
    benign_patterns: list[str],
    ambiguity_patterns: list[str],
    timeline_context: str,
) -> list[EvidenceItem]:
    digest = [
        EvidenceItem(
            kind="vision",
            claim=(packet.vision.description or "No scene description available").strip()[:180],
            confidence=packet.vision.confidence,
            source="vision",
            status="supporting",
        ),
        EvidenceItem(
            kind="risk",
            claim=", ".join(threat_patterns or benign_patterns or packet.vision.risk_labels[:2] or ["no strong ontology tag"]),
            confidence=max(packet.vision.confidence, 0.35),
            source="policy",
            status="supporting" if threat_patterns else "counter" if benign_patterns else "missing",
        ),
        EvidenceItem(
            kind="history",
            claim=timeline_context,
            confidence=min(1.0, 0.35 + max(packet.history.anomaly_score, 0.0) / 3),
            source="history",
            status="supporting" if packet.history.recent_events or packet.history.similar_events else "missing",
        ),
    ]
    if ambiguity_patterns:
        digest.append(
            EvidenceItem(
                kind="uncertainty",
                claim=", ".join(ambiguity_patterns),
                confidence=packet.vision.uncertainty,
                source="case_engine",
                status="missing" if "missing_historical_context" in ambiguity_patterns else "counter",
            )
        )

    alert_votes = sum(1 for output in agent_outputs if output.verdict == "alert")
    suppress_votes = sum(1 for output in agent_outputs if output.verdict == "suppress")
    digest.append(
        EvidenceItem(
            kind="consensus",
            claim=f"{alert_votes} alert, {suppress_votes} suppress, decision={routing.action}",
            confidence=max((max(alert_votes, suppress_votes) / max(len(agent_outputs), 1)), 0.25),
            source="reasoning_agents",
            status="supporting",
        )
    )
    if decision_reasoning:
        digest.append(
            EvidenceItem(
                kind="policy",
                claim=decision_reasoning[:180],
                confidence=1.0,
                source="arbiter",
                status="supporting",
            )
        )
    return digest[:5]


def _uncertainty_state(packet: FramePacket, ambiguity_patterns: list[str]) -> str:
    if packet.vision.uncertainty >= 0.65 or "conflicting_evidence" in ambiguity_patterns:
        return "high"
    if packet.vision.uncertainty >= 0.35 or ambiguity_patterns:
        return "medium"
    return "low"


def _identity_metadata(packet: FramePacket) -> dict:
    if not packet.event_context or not isinstance(packet.event_context.metadata, dict):
        return {}
    metadata = packet.event_context.metadata
    trusted: dict[str, object] = {}
    for key in ("known_person", "familiar_face", "trusted_visitor", "identity_source"):
        if key in metadata:
            trusted[key] = metadata.get(key)
    return trusted


def _perception_contract(packet: FramePacket) -> PerceptionEvidence:
    identity_metadata = _identity_metadata(packet)
    observed = [
        EvidenceItem(
            kind="scene",
            claim=(packet.vision.description or "No scene description available").strip()[:180],
            confidence=packet.vision.confidence,
            source="vision",
            status="supporting",
        )
    ]
    if packet.vision.evidence_notes:
        for note in packet.vision.evidence_notes[:2]:
            observed.append(
                EvidenceItem(
                    kind="observation",
                    claim=note,
                    confidence=packet.vision.confidence,
                    source="vision",
                    status="supporting",
                )
            )
    if packet.vision.observed_actions:
        observed.append(
            EvidenceItem(
                kind="action",
                claim=", ".join(packet.vision.observed_actions[:3]),
                confidence=packet.vision.confidence,
                source="vision",
                status="supporting",
            )
        )
    if packet.vision.visibility_tags:
        observed.append(
            EvidenceItem(
                kind="visibility",
                claim=", ".join(packet.vision.visibility_tags[:3]),
                confidence=max(packet.vision.uncertainty, 0.2),
                source="vision",
                status="counter" if packet.vision.uncertainty >= 0.35 else "supporting",
            )
        )
    if packet.stream_meta.zone:
        observed.append(
            EvidenceItem(
                kind="zone",
                claim=f"zone={packet.stream_meta.zone}",
                confidence=1.0,
                source="stream_meta",
                status="supporting",
            )
        )
    return PerceptionEvidence(
        categories=list(packet.vision.categories),
        risk_cues=list(packet.vision.risk_labels),
        uncertainty_state=_uncertainty_state(packet, []),
        observed_evidence=observed,
        prohibited_inference_compliant=True,
        upstream_identity_metadata=identity_metadata,
    )


def _explanation_contract(
    *,
    packet: FramePacket,
    routing: MachineRouting,
    threat_patterns: list[str],
    benign_patterns: list[str],
    ambiguity_patterns: list[str],
    timeline_context: str,
    decision_reasoning: str,
) -> ExplanationContract:
    observed = [
        EvidenceItem(
            kind="observed_scene",
            claim=(packet.vision.description or "No scene description available").strip()[:180],
            confidence=packet.vision.confidence,
            source="vision",
            status="supporting",
        ),
        EvidenceItem(
            kind="observed_actions",
            claim=", ".join(packet.vision.observed_actions or ["unclear_action"]),
            confidence=packet.vision.confidence,
            source="vision",
            status="supporting",
        ),
        EvidenceItem(
            kind="scene_categories",
            claim=", ".join(packet.vision.categories or ["clear"]),
            confidence=packet.vision.confidence,
            source="vision",
            status="supporting",
        ),
    ]
    if packet.vision.visibility_tags:
        observed.append(
            EvidenceItem(
                kind="visibility_limits",
                claim=", ".join(packet.vision.visibility_tags[:3]),
                confidence=max(packet.vision.uncertainty, 0.35),
                source="vision",
                status="counter" if packet.vision.uncertainty >= 0.35 else "supporting",
            )
        )
    if packet.vision.evidence_notes:
        observed.extend(
            [
                EvidenceItem(
                    kind="visible_fact",
                    claim=note,
                    confidence=packet.vision.confidence,
                    source="vision",
                    status="supporting",
                )
                for note in packet.vision.evidence_notes[:2]
            ]
        )
    benign = [
        EvidenceItem(
            kind="benign_pattern",
            claim=pattern.replace("_", " "),
            confidence=max(0.45, 1.0 - packet.vision.uncertainty),
            source="case_engine",
            status="counter",
        )
        for pattern in benign_patterns[:3]
    ]
    threat = [
        EvidenceItem(
            kind="threat_pattern",
            claim=pattern.replace("_", " "),
            confidence=max(packet.vision.confidence, 0.55),
            source="case_engine",
            status="supporting",
        )
        for pattern in threat_patterns[:3]
    ]
    uncertainty = [
        EvidenceItem(
            kind="uncertainty_pattern",
            claim=pattern.replace("_", " "),
            confidence=max(packet.vision.uncertainty, 0.35),
            source="case_engine",
            status="missing" if "missing" in pattern else "counter",
        )
        for pattern in ambiguity_patterns[:3]
    ]
    missing_information: list[str] = []
    if not packet.history.recent_events and not packet.history.similar_events:
        missing_information.append("limited historical context")
    if packet.vision.uncertainty >= 0.4:
        missing_information.append("partial or low-confidence scene visibility")
    if not _identity_metadata(packet):
        missing_information.append("no trusted identity metadata from upstream source")
    return ExplanationContract(
        observed_evidence=observed,
        benign_evidence=benign,
        threat_evidence=threat,
        uncertainty_evidence=uncertainty,
        routing_basis=[
            f"action={routing.action}",
            f"risk_level={routing.risk_level}",
            f"visibility={routing.visibility_policy}",
            f"notification={routing.notification_policy}",
            timeline_context,
            decision_reasoning[:180],
        ],
        missing_information=missing_information[:3],
    )


def _judgement_contract(
    *,
    packet: FramePacket,
    routing: MachineRouting,
    decision_confidence: float,
    decision_reasoning: str,
    explanation: ExplanationContract,
    agent_outputs: Sequence[AgentOutput],
) -> JudgementDecision:
    alert_votes = sum(1 for output in agent_outputs if output.verdict == "alert")
    suppress_votes = sum(1 for output in agent_outputs if output.verdict == "suppress")
    contradiction_markers: list[str] = []
    if alert_votes and suppress_votes:
        contradiction_markers.append("mixed_agent_votes")
    if packet.vision.threat and routing.action == "suppress":
        contradiction_markers.append("threat_scene_suppressed")
    if not packet.vision.threat and routing.action == "alert":
        contradiction_markers.append("non_threat_scene_alerted")
    return JudgementDecision(
        action=routing.action,
        risk_level=routing.risk_level,
        confidence=round(decision_confidence, 4),
        uncertainty_state=_uncertainty_state(packet, [item.claim for item in explanation.uncertainty_evidence]),
        evidence=explanation,
        decision_rationale=decision_reasoning,
        contradiction_markers=contradiction_markers,
    )


def _routing_contract(
    *,
    routing: MachineRouting,
    recommended_delivery_targets: list[str],
    consumer_summary: ConsumerSummary,
    operator_summary: OperatorSurfaceSummary,
    threat_patterns: list[str],
    benign_patterns: list[str],
    ambiguity_patterns: list[str],
) -> RoutingDecision:
    basis = [
        f"notification_policy={routing.notification_policy}",
        f"visibility_policy={routing.visibility_policy}",
        f"storage_policy={routing.storage_policy}",
    ]
    if threat_patterns:
        basis.append(f"threat_patterns={','.join(threat_patterns[:2])}")
    if benign_patterns:
        basis.append(f"benign_patterns={','.join(benign_patterns[:2])}")
    if ambiguity_patterns:
        basis.append(f"ambiguity_patterns={','.join(ambiguity_patterns[:2])}")
    return RoutingDecision(
        visibility_policy=routing.visibility_policy,
        notification_policy=routing.notification_policy,
        storage_policy=routing.storage_policy,
        delivery_targets=list(recommended_delivery_targets),
        operator_summary=operator_summary.recommended_next_step,
        homeowner_summary=consumer_summary.action_now,
        routing_basis=basis,
    )


def _action_readiness_contract(
    *,
    routing: MachineRouting,
    recommended_delivery_targets: list[str],
    decision_confidence: float,
    ambiguity_state: str,
    threat_patterns: list[str],
) -> ActionReadiness:
    if routing.action == "alert" and routing.risk_level == "high" and decision_confidence >= 0.75:
        autonomy_eligible = "human_confirmation"
    elif routing.action == "suppress" and routing.risk_level in {"none", "low"} and ambiguity_state == "resolved":
        autonomy_eligible = "low_risk_later"
    else:
        autonomy_eligible = "not_eligible"

    allowed_action_types: list[str] = ["create_incident"]
    required_confirmations: list[str] = []
    if routing.notification_policy in {"review", "immediate"}:
        allowed_action_types.append("notify")
    if routing.risk_level == "high":
        allowed_action_types.append("escalate_monitoring")
        required_confirmations.append("operator_review")
    elif autonomy_eligible == "low_risk_later":
        allowed_action_types.append("request_verification")

    action_intents = [
        ActionIntent(
            action_type="escalate_monitoring" if target == "monitoring" else "notify",
            target_type=target if target in {"webhook", "operator_queue", "homeowner_app", "monitoring", "timeline"} else "smart_home_adapter",
            target=target,
            summary=f"{routing.action}:{routing.risk_level}:{target}",
            dry_run=True,
        )
        for target in recommended_delivery_targets
    ]
    if threat_patterns and "create_incident" not in allowed_action_types:
        allowed_action_types.append("create_incident")

    return ActionReadiness(
        autonomy_eligible=autonomy_eligible,
        allowed_action_types=allowed_action_types,
        required_confirmations=required_confirmations,
        tool_targets=list(recommended_delivery_targets),
        action_intents=action_intents,
    )


def _location_for_display(packet: FramePacket) -> str | None:
    """Return location/camera name only when JSON explicitly mentions it (vision or stream metadata)."""
    # Vision explicitly mentions location
    if packet.vision.setting and packet.vision.setting != "unknown":
        return packet.vision.setting.replace("_", " ")
    if packet.vision.spatial_tags:
        for tag in packet.vision.spatial_tags:
            if tag and tag != "unknown_location":
                return tag.replace("_", " ")
    # Stream metadata has explicit camera/location name (not generic)
    label = (packet.stream_meta.label or "").strip()
    if label and label.lower() not in ("test camera", "camera", "cam", "pipeline_test", ""):
        return label
    return None


def _consumer_summary(
    *,
    packet: FramePacket,
    agent_outputs: Sequence[AgentOutput],
    case_status: str,
    confidence_band: str,
    threat_patterns: list[str],
    benign_patterns: list[str],
    recommended_next_action: str,
) -> ConsumerSummary:
    executive_output = _executive_output(agent_outputs)
    if executive_output and (executive_output.consumer_headline or executive_output.consumer_reason):
        action_now = recommended_next_action.split(".", 1)[0]
        headline = executive_output.consumer_headline or "Security activity"
        reason = executive_output.consumer_reason or "Review the latest event details."
        return ConsumerSummary(
            headline=headline[:80],
            reason=reason[:90],
            action_now=action_now[:90],
        )

    loc = _location_for_display(packet)
    identity = _first_label(packet.vision.identity_labels, packet.vision.categories, fallback="activity")
    risk_label = (threat_patterns or benign_patterns or packet.vision.risk_labels or ["no clear pattern"])[0].replace("_", " ")
    if case_status in {"active_threat", "urgent"}:
        headline = f"Urgent security review for {loc}" if loc else "Urgent security review"
        reason = f"{identity} linked to {risk_label}."
    elif case_status == "verify":
        headline = f"Check recent activity at {loc}" if loc else "Check recent activity"
        reason = f"{identity} may need verification because of {risk_label}."
    elif case_status in {"watch", "interesting"}:
        headline = f"Keep an eye on {loc}" if loc else "Keep an eye on"
        reason = f"{identity} is notable but still being evaluated."
    elif case_status == "closed_benign":
        headline = f"Benign activity logged at {loc}" if loc else "Benign activity logged"
        reason = f"{identity} looks consistent with {risk_label}."
    else:
        headline = f"Routine activity at {loc}" if loc else "Routine activity"
        reason = f"{identity} does not currently indicate a threat."
    action_now = recommended_next_action.split(".", 1)[0]
    if len(f"{headline} {reason} {action_now}") > 180:
        reason = reason[: max(0, 176 - len(headline) - len(action_now))].rstrip()
    return ConsumerSummary(headline=headline[:80], reason=reason[:90], action_now=action_now[:90])


def _operator_summary(
    *,
    packet: FramePacket,
    agent_outputs: Sequence[AgentOutput],
    routing: MachineRouting,
    threat_patterns: list[str],
    benign_patterns: list[str],
    ambiguity_patterns: list[str],
    timeline_context: str,
    recommended_next_action: str,
) -> OperatorSurfaceSummary:
    executive_output = _executive_output(agent_outputs)
    identity = _first_label(packet.vision.identity_labels, packet.vision.categories, fallback="activity")
    loc = _location_for_display(packet)
    loc_phrase = f" in {loc}" if loc else ""
    observed = (
        f"{identity}{loc_phrase}; categories={','.join(packet.vision.categories)}; "
        f"risk_labels={','.join(packet.vision.risk_labels or ['clear'])}."
    )
    why_flagged = (
        ", ".join(threat_patterns)
        if threat_patterns
        else f"routed as {routing.risk_level} risk with {packet.history.anomaly_score:.2f} anomaly signal"
    )
    why_not_benign = (
        "no dominant benign pattern"
        if not benign_patterns
        else f"benign alternatives considered: {', '.join(benign_patterns)}"
    )
    if executive_output and executive_output.operator_observed:
        observed = executive_output.operator_observed
    if executive_output and executive_output.operator_triage:
        why_flagged = executive_output.operator_triage
    what_is_uncertain = ", ".join(ambiguity_patterns) if ambiguity_patterns else "low ambiguity in current case state"
    return OperatorSurfaceSummary(
        what_observed=observed[:220],
        why_flagged=why_flagged[:220],
        why_not_benign=why_not_benign[:220],
        what_is_uncertain=what_is_uncertain[:220],
        timeline_context=timeline_context[:220],
        recommended_next_step=recommended_next_action[:220],
    )


def _first_label(primary: Sequence[str], secondary: Sequence[str], *, fallback: str) -> str:
    for label in list(primary) + list(secondary):
        if label and label != "clear":
            return label.replace("_", " ")
    return fallback
