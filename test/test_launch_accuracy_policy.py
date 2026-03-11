from __future__ import annotations

import json
from pathlib import Path

from backend.actions import build_action_bus_payload
from backend.agent.reasoning.arbiter import _compute_verdict
from backend.agent.reasoning.base import _guardrail_failure_reason
from backend.config import settings
from backend.models.schemas import AgentOutput, EventContext, FramePacket, HistoryContext, StreamMeta, VisionResult
from backend.policy import BLESSED_STACK, POLICY_VERSION, PROMPT_VERSION, RELEASE_LATENCY_BUDGET_MS


def _packet(*, zone: str, hour: int, categories: list[str], risk_labels: list[str], threat: bool, uncertainty: float = 0.1) -> FramePacket:
    from datetime import datetime

    return FramePacket(
        frame_id="evt-1",
        stream_id="cam-1",
        timestamp=datetime(2026, 3, 8, hour, 0, 0),
        b64_frame="",
        stream_meta=StreamMeta(stream_id="cam-1", label="Front", site_id="home", zone=zone, uri="direct"),
        vision=VisionResult(
            threat=threat,
            severity="high" if threat else "none",
            categories=categories,
            identity_labels=["person"] if "person" in categories else categories,
            risk_labels=risk_labels,
            uncertainty=uncertainty,
            description="test scene",
            confidence=0.8,
            bbox=[],
        ),
        history=HistoryContext(),
        event_context=EventContext(source="test", metadata={}),
    )


def _agent(verdict: str, risk_level: str, confidence: float, rationale: str) -> AgentOutput:
    return AgentOutput(
        agent_id="test_agent",
        role="tester",
        verdict=verdict,
        risk_level=risk_level,
        confidence=confidence,
        rationale=rationale,
        recommended_action="continue monitoring",
        chain_notes={},
    )


def test_reasoning_guardrail_rejects_identity_inference():
    output = _agent(
        "suppress",
        "low",
        0.7,
        "SIGNAL: routine person. EVIDENCE: resident returning home. UNCERTAINTY: low. DECISION: suppress as known resident.",
    )
    assert _guardrail_failure_reason(output) == "privacy_policy: inferred_identity"


def test_arbiter_escalates_explicit_tamper_signal():
    packet = _packet(zone="garage", hour=22, categories=["person"], risk_labels=["tamper"], threat=True)
    outputs = [
        _agent("suppress", "low", 0.8, "SIGNAL: low. EVIDENCE: routine motion observed. UNCERTAINTY: some. DECISION: suppress."),
        _agent("suppress", "low", 0.7, "SIGNAL: low. EVIDENCE: no direct threat details. UNCERTAINTY: some. DECISION: suppress."),
    ]
    verdict = _compute_verdict(packet, outputs, "")
    assert verdict.routing.action == "alert"
    assert verdict.routing.risk_level in {"medium", "high"}


def test_arbiter_guardrail_escalation_preserves_alert_when_risk_level_medium():
    """Guardrails escalate explicit threat (suspicious_person) to alert+medium; action must stay alert."""
    packet = _packet(
        zone="front_door",
        hour=14,
        categories=["person"],
        risk_labels=["suspicious_person"],
        threat=False,
    )
    outputs = [
        _agent("suppress", "low", 0.8, "SIGNAL: low. EVIDENCE: routine. UNCERTAINTY: some. DECISION: suppress."),
        _agent("suppress", "low", 0.7, "SIGNAL: low. EVIDENCE: no threat. UNCERTAINTY: some. DECISION: suppress."),
    ]
    verdict = _compute_verdict(packet, outputs, "")
    assert verdict.routing.action == "alert"
    assert verdict.routing.visibility_policy == "prominent"
    assert verdict.routing.notification_policy == "immediate"


def test_arbiter_escalates_wildlife_near_entry_when_grounded_alert_support_exists():
    packet = _packet(
        zone="front_door",
        hour=1,
        categories=["pet"],
        risk_labels=["wildlife_near_entry"],
        threat=False,
    )
    outputs = [
        AgentOutput(
            agent_id="context_baseline_reasoner",
            role="context",
            verdict="alert",
            risk_level="medium",
            confidence=0.82,
            rationale="SIGNAL: wildlife near entry. EVIDENCE: large animal at threshold. UNCERTAINTY: exact species unknown. DECISION: alert.",
            recommended_action="review promptly",
            chain_notes={},
        ),
        AgentOutput(
            agent_id="trajectory_intent_assessor",
            role="trajectory",
            verdict="alert",
            risk_level="medium",
            confidence=0.74,
            rationale="SIGNAL: wildlife near entry. EVIDENCE: animal at door area. UNCERTAINTY: exact behavior limited. DECISION: alert.",
            recommended_action="review promptly",
            chain_notes={},
        ),
        _agent("uncertain", "low", 0.0, "SIGNAL: unclear. EVIDENCE: limited. UNCERTAINTY: some. DECISION: uncertain."),
    ]
    verdict = _compute_verdict(packet, outputs, "")
    assert verdict.routing.action == "alert"
    assert verdict.routing.risk_level == "high"


def test_manifest_matches_policy_versions_and_budget():
    manifest_path = Path(__file__).parent / "fixtures" / "eval" / "home_security" / "launch_accuracy_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["policy_version"] == POLICY_VERSION
    assert manifest["prompt_version"] == PROMPT_VERSION
    assert manifest["latency_budget_ms"]["pipeline_p95"] == int(RELEASE_LATENCY_BUDGET_MS["pipeline_p95"])


def test_holdout_catalog_matches_prompt_version_and_expected_shape():
    catalog_path = (
        Path(__file__).parent
        / "fixtures"
        / "eval"
        / "home_security"
        / "synthetic"
        / "synthetic_vision_holdout_catalog.json"
    )
    catalog = json.loads(catalog_path.read_text())
    scenarios = catalog["scenarios"]
    assert catalog["policy_version"] == POLICY_VERSION
    assert catalog["prompt_version"] == PROMPT_VERSION
    assert len(scenarios) == 15
    assert len({scenario["scenario_id"] for scenario in scenarios}) == 15
    assert BLESSED_STACK["reasoning_provider"] == "groq"
    assert BLESSED_STACK["reasoning_model"] == "qwen/qwen3-32b"


def test_action_bus_payload_reflects_action_readiness_contract():
    packet = _packet(zone="front_door", hour=23, categories=["person"], risk_labels=["entry_dwell"], threat=True)
    outputs = [
        _agent("alert", "high", 0.91, "SIGNAL: entry risk. EVIDENCE: person lingering at entry. UNCERTAINTY: low. DECISION: alert."),
        _agent("alert", "high", 0.82, "SIGNAL: suspicious presence. EVIDENCE: prolonged dwell. UNCERTAINTY: low. DECISION: alert."),
    ]
    verdict = _compute_verdict(packet, outputs, "")
    payload = build_action_bus_payload(verdict)
    assert payload["autonomy_eligible"] == verdict.action_readiness.autonomy_eligible
    assert "notify" in payload["allowed_action_types"]
    assert payload["tool_targets"]
    assert payload["action_intents"]


# ---------------------------------------------------------------------------
# Release gate: latency, token, and config contracts
# These tests enforce the concrete production constraints every release must meet.
# ---------------------------------------------------------------------------

def test_release_latency_budget_contract():
    """Latency budgets in policy.py must match the hard limits the pipeline enforces."""
    assert RELEASE_LATENCY_BUDGET_MS["pipeline_p95"] == 3000.0, (
        "Pipeline p95 budget must be 3000ms — increase only if benchmarks prove it"
    )
    assert RELEASE_LATENCY_BUDGET_MS["reasoning_p95"] == 1200.0, (
        "Reasoning p95 budget must be 1200ms — Phase 1 parallel target"
    )
    assert RELEASE_LATENCY_BUDGET_MS["vision_p95"] == 1200.0


def test_release_token_budget_contract():
    """Max token caps must stay within cost targets.
    
    At 600 tokens/agent x 4 agents = 2400 max completion tokens per pipeline run.
    Prompt tokens are ~1500/agent = ~6000 total. At Groq rates this keeps cost minimal.
    Any increase must be deliberate and benchmarked.
    """
    assert settings.groq_reasoning_max_tokens <= 600, (
        "groq_reasoning_max_tokens > 600 — raises cost per pipeline run, needs benchmarking"
    )
    assert settings.cerebras_max_completion_tokens <= 1000
    assert settings.together_reasoning_max_tokens <= 1000
    assert settings.siliconflow_reasoning_max_tokens <= 1000


def test_groq_thinking_disabled_by_default():
    """Groq Qwen3 thinking must be disabled in production default.
    
    reasoning_effort=none eliminates <think> tokens entirely: ~500ms vs ~1500ms,
    ~200 tokens vs ~600 tokens per agent call. Enable only for explicit debugging.
    """
    assert settings.groq_enable_thinking is False, (
        "groq_enable_thinking must be False for production — "
        "set GROQ_ENABLE_THINKING=true only for local debugging"
    )


def test_blessed_stack_is_groq_qwen3():
    """Blessed stack must stay groq/qwen3-32b until a new model is formally benchmarked and promoted."""
    assert BLESSED_STACK["reasoning_provider"] == "groq"
    assert BLESSED_STACK["reasoning_model"] == "qwen/qwen3-32b"
    assert BLESSED_STACK["vision_provider"] == "siliconflow"


# ---------------------------------------------------------------------------
# Release gate: accuracy — deterministic scenario verdicts from launch manifest
# These use the arbiter's _compute_verdict directly so no LLM is required.
# Each scenario enforces the hard guardrail rules that must hold regardless of model.
# ---------------------------------------------------------------------------

_MANIFEST = json.loads(
    (Path(__file__).parent / "fixtures" / "eval" / "home_security" / "launch_accuracy_manifest.json").read_text()
)
_SCENARIOS = {s["scenario_id"]: s for s in _MANIFEST["scenarios"]}


def _make_scenario_packet(scenario_id: str) -> FramePacket:
    from datetime import datetime

    s = _SCENARIOS[scenario_id]
    ts = datetime.fromisoformat(s["time_context"])
    # Map scenario to threat signals
    risk_labels: list[str] = []
    categories: list[str] = ["person"]
    threat = s["expected_action"] == "alert"
    if scenario_id == "tamper_signal":
        risk_labels = ["tamper"]
    elif scenario_id == "after_hours_unknown_person":
        risk_labels = ["entry_approach", "suspicious_presence"]
    elif scenario_id == "routine_arrival_daytime":
        risk_labels = ["delivery_pattern"]
        threat = False
    elif scenario_id == "package_delivery":
        categories = ["package", "person"]
        risk_labels = ["delivery_pattern"]
        threat = False
    elif scenario_id == "pet_motion":
        categories = ["pet"]
        risk_labels = []
        threat = False
    elif scenario_id == "poor_visibility_person":
        risk_labels = ["entry_approach"]
    return FramePacket(
        frame_id="gate-" + scenario_id,
        stream_id="cam-gate",
        timestamp=ts,
        b64_frame="",
        stream_meta=StreamMeta(
            stream_id="cam-gate",
            label="Gate Cam",
            site_id="home",
            zone=s["zone"],
            uri="direct",
        ),
        vision=VisionResult(
            threat=threat,
            severity="high" if threat else "none",
            categories=categories,
            identity_labels=categories,
            risk_labels=risk_labels,
            uncertainty=0.3 if s.get("expected_uncertainty") == "high" else 0.1,
            description="accuracy gate scenario",
            confidence=0.8,
            bbox=[],
        ),
        history=HistoryContext(),
        event_context=EventContext(source="gate", metadata={}),
    )


def test_accuracy_gate_tamper_signal_must_alert():
    s = _SCENARIOS["tamper_signal"]
    packet = _make_scenario_packet("tamper_signal")
    outputs = [
        _agent("suppress", "low", 0.8, "SIGNAL: motion. EVIDENCE: routine check. UNCERTAINTY: some. DECISION: suppress."),
        _agent("suppress", "low", 0.7, "SIGNAL: low. EVIDENCE: no direct threat. UNCERTAINTY: some. DECISION: suppress."),
    ]
    verdict = _compute_verdict(packet, outputs, "")
    assert verdict.routing.action == s["expected_action"], (
        "tamper_signal must always alert regardless of agent votes — hard guardrail"
    )
    assert verdict.routing.risk_level in {"medium", "high"}


def test_accuracy_gate_after_hours_unknown_must_alert():
    s = _SCENARIOS["after_hours_unknown_person"]
    packet = _make_scenario_packet("after_hours_unknown_person")
    outputs = [
        _agent("alert", "high", 0.85, "SIGNAL: after-hours unknown. EVIDENCE: person at entry 11pm. UNCERTAINTY: low. DECISION: alert."),
        _agent("alert", "medium", 0.75, "SIGNAL: suspicious presence. EVIDENCE: unrecognised approach. UNCERTAINTY: low. DECISION: alert."),
        _agent("uncertain", "medium", 0.5, "SIGNAL: unclear. EVIDENCE: limited. UNCERTAINTY: high. DECISION: uncertain."),
    ]
    verdict = _compute_verdict(packet, outputs, "")
    assert verdict.routing.action == s["expected_action"], (
        "after_hours unknown presence at entry must produce alert"
    )


def test_accuracy_gate_routine_arrival_must_suppress():
    s = _SCENARIOS["routine_arrival_daytime"]
    packet = _make_scenario_packet("routine_arrival_daytime")
    outputs = [
        _agent("suppress", "low", 0.9, "SIGNAL: routine. EVIDENCE: daytime delivery pattern. UNCERTAINTY: low. DECISION: suppress."),
        _agent("suppress", "low", 0.85, "SIGNAL: benign. EVIDENCE: direct approach with package. UNCERTAINTY: low. DECISION: suppress."),
        _agent("suppress", "low", 0.8, "SIGNAL: benign hypothesis holds. EVIDENCE: delivery pattern matches. UNCERTAINTY: low. DECISION: suppress."),
    ]
    verdict = _compute_verdict(packet, outputs, "")
    assert verdict.routing.action == s["expected_action"], (
        "routine daytime arrival must suppress — false positive critical"
    )


def test_accuracy_gate_package_delivery_must_suppress():
    s = _SCENARIOS["package_delivery"]
    packet = _make_scenario_packet("package_delivery")
    outputs = [
        _agent("suppress", "low", 0.92, "SIGNAL: routine delivery. EVIDENCE: package at porch in daytime. UNCERTAINTY: low. DECISION: suppress."),
        _agent("suppress", "low", 0.88, "SIGNAL: benign. EVIDENCE: delivery pattern. UNCERTAINTY: low. DECISION: suppress."),
    ]
    verdict = _compute_verdict(packet, outputs, "")
    assert verdict.routing.action == s["expected_action"], (
        "package delivery must suppress — false positive critical"
    )
