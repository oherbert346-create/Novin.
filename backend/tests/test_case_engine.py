from __future__ import annotations

from datetime import datetime, timedelta

from backend.agent.case_engine import build_case_state, _location_for_display
from backend.models.schemas import (
    AgentOutput,
    AuditTrail,
    EventContext,
    FramePacket,
    HistoryContext,
    LiabilityDigest,
    MachineRouting,
    OperatorSummary,
    RecentEvent,
    StreamMeta,
    Verdict,
    VisionResult,
)
from backend.public import public_case_fields


def test_build_case_state_produces_structured_threat_case() -> None:
    now = datetime.utcnow()
    packet = FramePacket(
        frame_id="frame-threat-1",
        stream_id="cam-front",
        timestamp=now,
        b64_frame="abc",
        stream_meta=StreamMeta(
            stream_id="cam-front",
            label="Front Door",
            site_id="home-uk",
            zone="front_door",
            uri="direct",
        ),
        vision=VisionResult(
            threat=True,
            severity="high",
            categories=["person", "intrusion"],
            identity_labels=["person"],
            risk_labels=["forced_entry", "tamper"],
            description="unknown person forcing the front door lock",
            confidence=0.96,
        ),
        history=HistoryContext(
            recent_events=[
                RecentEvent(
                    event_id="evt-prev-1",
                    stream_id="cam-driveway",
                    timestamp=now - timedelta(minutes=4),
                    severity="medium",
                    categories=["person"],
                    description="person approaching driveway",
                    event_context={"metadata": {"case_id": "older-case"}},
                )
            ],
            anomaly_score=0.82,
        ),
        event_context=EventContext(
            source="webhook",
            source_event_id="src-threat-1",
            cam_id="cam-front",
            home_id="home-uk",
            zone="front_door",
            metadata={"scenario_id": "uk-threat-case-1"},
        ),
    )
    agent_outputs = [
        AgentOutput(
            agent_id="context_baseline_reasoner",
            role="Threat Escalation",
            verdict="alert",
            risk_level="high",
            confidence=0.94,
            rationale="SIGNAL: forced entry. EVIDENCE: repeated lock tampering. UNCERTAINTY: identity unknown. DECISION: alert.",
            recommended_action="notify immediately",
        ),
        AgentOutput(
            agent_id="trajectory_intent_assessor",
            role="Behavioral Pattern",
            verdict="alert",
            risk_level="high",
            confidence=0.88,
            rationale="SIGNAL: focused entry attack. EVIDENCE: repeated contact with front door. UNCERTAINTY: no tool label. DECISION: alert.",
            recommended_action="review promptly",
        ),
        AgentOutput(
            agent_id="falsification_auditor",
            role="Adversarial Challenger",
            verdict="suppress",
            risk_level="low",
            confidence=0.12,
            rationale="SIGNAL: weak benign alternative. EVIDENCE: could be a resident lock issue. UNCERTAINTY: low support. DECISION: suppress.",
            recommended_action="keep in timeline only",
        ),
    ]
    routing = MachineRouting(
        is_threat=True,
        action="alert",
        risk_level="high",
        severity="high",
        categories=["person", "intrusion"],
        visibility_policy="prominent",
        notification_policy="immediate",
        storage_policy="full",
    )

    case_state = build_case_state(
        packet=packet,
        agent_outputs=agent_outputs,
        routing=routing,
        decision_confidence=0.93,
        decision_reasoning="Forced entry indicators at the front door require immediate escalation.",
    )

    assert case_state.case_id == "uk-threat-case-1"
    assert case_state.case_status == "active_threat"
    assert case_state.ambiguity_state in {"monitoring", "resolved"}
    assert case_state.confidence_band == "high"
    assert "forced_entry" in case_state.threat_patterns
    assert "tamper" in case_state.threat_patterns
    assert case_state.consumer_summary.headline
    assert case_state.consumer_summary.action_now
    assert case_state.operator_summary.what_observed
    assert case_state.operator_summary.recommended_next_step.startswith("Escalate immediately")
    assert len(case_state.evidence_digest) >= 4
    assert case_state.recommended_delivery_targets == ["homeowner_app", "operator_queue", "webhook", "monitoring"]
    assert case_state.perception.categories == ["person", "intrusion"]
    assert case_state.judgement.action == "alert"
    assert case_state.judgement.risk_level == "high"
    assert case_state.judgement.evidence.threat_evidence
    assert case_state.routing_decision.delivery_targets == ["homeowner_app", "operator_queue", "webhook", "monitoring"]
    assert case_state.action_readiness.autonomy_eligible == "human_confirmation"
    assert "notify" in case_state.action_readiness.allowed_action_types
    assert case_state.action_readiness.action_intents


def test_public_case_fields_backfill_legacy_verdict_contract() -> None:
    verdict = Verdict(
        frame_id="legacy-frame-1",
        event_id="legacy-event-1",
        stream_id="cam-driveway",
        site_id="home-uk",
        timestamp=datetime.utcnow(),
        routing=MachineRouting(
            is_threat=False,
            action="suppress",
            risk_level="low",
            severity="low",
            categories=["person"],
            visibility_policy="timeline",
            notification_policy="none",
            storage_policy="timeline",
        ),
        summary=OperatorSummary(
            headline="Person observed on the driveway.",
            narrative="Legacy path marked this as low-risk activity pending continued monitoring.",
        ),
        audit=AuditTrail(
            liability_digest=LiabilityDigest(
                decision_reasoning="Low-risk activity with no immediate escalation requirement.",
                confidence_score=0.67,
            ),
            agent_outputs=[],
        ),
        description="single person walking across the driveway",
        bbox=[],
        b64_thumbnail="",
        event_context=EventContext(
            source="frigate",
            source_event_id="legacy-source-1",
            cam_id="cam-driveway",
            home_id="home-uk",
            zone="driveway",
        ),
    )

    payload = public_case_fields(verdict)

    assert payload["case_id"] == "legacy-source-1"
    assert payload["case_status"] == "watch"
    assert payload["ambiguity_state"] == "resolved"
    assert payload["confidence_band"] == "medium"
    assert payload["consumer_summary"]["headline"]
    assert payload["consumer_summary"]["reason"]
    assert payload["consumer_summary"]["action_now"]
    assert payload["operator_summary"]["what_observed"]
    assert payload["operator_summary"]["why_flagged"]
    assert payload["operator_summary"]["timeline_context"]
    assert payload["operator_summary"]["recommended_next_step"]
    assert len(payload["evidence_digest"]) >= 3
    assert payload["recommended_delivery_targets"] == ["timeline"]
    assert payload["case"]["consumer_summary"] == payload["consumer_summary"]
    assert payload["case"]["operator_summary"] == payload["operator_summary"]
    assert payload["perception"]["categories"] == []
    assert payload["judgement"]["action"] == "suppress"
    assert payload["routing_decision"]["delivery_targets"] == []
    assert payload["action_readiness"]["autonomy_eligible"] == "not_eligible"


def _make_packet(
    *,
    stream_label: str = "Test Camera",
    zone: str = "front_door",
    vision_setting: str = "unknown",
    spatial_tags: list[str] | None = None,
) -> FramePacket:
    now = datetime.utcnow()
    return FramePacket(
        frame_id="frame-1",
        stream_id="cam-1",
        timestamp=now,
        b64_frame="abc",
        stream_meta=StreamMeta(
            stream_id="cam-1",
            label=stream_label,
            site_id="home-uk",
            zone=zone,
            uri="direct",
        ),
        vision=VisionResult(
            threat=False,
            severity="low",
            categories=["person"],
            identity_labels=["person"],
            risk_labels=[],
            description="person walking",
            confidence=0.9,
            setting=vision_setting,
            spatial_tags=spatial_tags or [],
        ),
        history=HistoryContext(recent_events=[], anomaly_score=0.0),
        event_context=EventContext(
            source="webhook",
            source_event_id="evt-1",
            cam_id="cam-1",
            home_id="home-uk",
            zone=zone,
        ),
    )


def test_location_for_display_from_vision_setting() -> None:
    """Location comes from vision.setting when not unknown."""
    packet = _make_packet(vision_setting="driveway", stream_label="Test Camera")
    assert _location_for_display(packet) == "driveway"


def test_location_for_display_from_spatial_tags() -> None:
    """Location comes from spatial_tags when setting is unknown."""
    packet = _make_packet(
        vision_setting="unknown",
        spatial_tags=["at_driveway"],
        stream_label="Test Camera",
    )
    assert _location_for_display(packet) == "at driveway"


def test_location_for_display_none_when_generic_label() -> None:
    """No location when label is generic and vision has no explicit location."""
    for label in ("Test Camera", "camera", "cam", "pipeline_test"):
        packet = _make_packet(stream_label=label, vision_setting="unknown")
        assert _location_for_display(packet) is None


def test_location_for_display_from_explicit_label() -> None:
    """Location comes from stream label when vision has no explicit location and label is not generic."""
    packet = _make_packet(stream_label="Driveway Cam", vision_setting="unknown")
    assert _location_for_display(packet) == "Driveway Cam"


def test_consumer_summary_headline_includes_location_when_available() -> None:
    """Headline includes location when _location_for_display returns a value."""
    packet = _make_packet(vision_setting="driveway", stream_label="Test Camera")
    case_state = build_case_state(
        packet=packet,
        agent_outputs=[],
        routing=MachineRouting(
            is_threat=False,
            action="suppress",
            risk_level="low",
            severity="low",
            categories=["person"],
            visibility_policy="timeline",
            notification_policy="none",
            storage_policy="timeline",
        ),
        decision_confidence=0.9,
        decision_reasoning="Low risk.",
    )
    assert "driveway" in case_state.consumer_summary.headline


def test_consumer_summary_headline_omits_location_when_unavailable() -> None:
    """Headline omits location when _location_for_display returns None (generic label, unknown vision)."""
    packet = _make_packet(stream_label="Test Camera", vision_setting="unknown")
    case_state = build_case_state(
        packet=packet,
        agent_outputs=[],
        routing=MachineRouting(
            is_threat=False,
            action="suppress",
            risk_level="low",
            severity="low",
            categories=["person"],
            visibility_policy="timeline",
            notification_policy="none",
            storage_policy="timeline",
        ),
        decision_confidence=0.9,
        decision_reasoning="Low risk.",
    )
    # Headline must not contain hardcoded "front door" when no explicit location
    assert "front door" not in case_state.consumer_summary.headline.lower()


def test_case_state_prefers_executive_triage_summaries_when_present() -> None:
    packet = _make_packet(stream_label="Porch Cam", vision_setting="porch_door")
    case_state = build_case_state(
        packet=packet,
        agent_outputs=[
            AgentOutput(
                agent_id="executive_triage_commander",
                role="Executive Triage",
                verdict="uncertain",
                risk_level="medium",
                confidence=0.66,
                rationale="SIGNAL: mixed cues. EVIDENCE: visible person near entry. UNCERTAINTY: intent unclear. DECISION: uncertain.",
                recommended_action="review promptly",
                consumer_headline="Check the porch activity",
                consumer_reason="A person near the entry may need verification.",
                operator_observed="Person observed near porch entry with limited context.",
                operator_triage="Final triage held at medium risk pending manual verification.",
                chain_notes={"focus": "executive_triage", "threat_outcome": "MEDIUM", "triage": "notify_user"},
            )
        ],
        routing=MachineRouting(
            is_threat=False,
            action="suppress",
            risk_level="medium",
            severity="medium",
            categories=["person"],
            visibility_policy="prominent",
            notification_policy="review",
            storage_policy="timeline",
        ),
        decision_confidence=0.66,
        decision_reasoning="Medium-risk triage decision.",
    )

    assert case_state.consumer_summary.headline == "Check the porch activity"
    assert case_state.consumer_summary.reason == "A person near the entry may need verification."
    assert case_state.operator_summary.what_observed == "Person observed near porch entry with limited context."
    assert case_state.operator_summary.why_flagged == "Final triage held at medium risk pending manual verification."
