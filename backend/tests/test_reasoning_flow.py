from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch

from backend.agent.bus import AgentMessageBus
from backend.agent.reasoning import arbiter
from backend.agent.reasoning.base import ReasoningAgent, _extract_json_content, _repair_truncated_json, _vision_agent_view
from backend.models.schemas import AgentOutput, FramePacket, HistoryContext, StreamMeta, VisionResult


class _RetryAgent(ReasoningAgent):
    agent_id = "retry_agent"
    role = "Retry Agent"
    system_prompt = "test"

    def __init__(self) -> None:
        self.calls = 0

    async def _call_model(self, client, user_content, prior_response=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "verdict": "invalid",
                "risk_level": "low",
                "confidence": 0.5,
                "rationale": "bad",
                "recommended_action": "continue monitoring",
                "chain_notes": {},
            }, "bad", None
        return {
            "verdict": "alert",
            "risk_level": "high",
            "confidence": 0.8,
            "rationale": (
                "SIGNAL: person moving toward entry point. "
                "EVIDENCE: vision reports intrusion with high confidence near door. "
                "UNCERTAINTY: identity is unknown. "
                "DECISION: alert aligns with observed threat cues."
            ),
            "recommended_action": "notify immediately and keep monitoring",
            "chain_notes": {},
        }, "ok", None

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        return "task"


class _PromptDriftAgent(ReasoningAgent):
    agent_id = "prompt_drift_agent"
    role = "Prompt Drift Agent"
    system_prompt = "test"

    async def _call_model(self, client, user_content, prior_response=None):
        return {
            "verdict": "alert",
            "risk_level": "medium",
            "confidence": 0.8,
            "rationale": "plain prose rationale without required sections",
            "recommended_action": "review promptly",
            "chain_notes": {},
        }, "ok", None

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        return "task"


class _LowEvidenceAgent(ReasoningAgent):
    agent_id = "low_evidence_agent"
    role = "Low Evidence Agent"
    system_prompt = "test"

    async def _call_model(self, client, user_content, prior_response=None):
        return {
            "verdict": "alert",
            "risk_level": "medium",
            "confidence": 0.92,
            "rationale": (
                "SIGNAL: suspicious movement. "
                "EVIDENCE: no evidence of identity or intent available. "
                "UNCERTAINTY: insufficient evidence to confirm intent. "
                "DECISION: alert anyway."
            ),
            "recommended_action": "review promptly",
            "chain_notes": {},
        }, "ok", None

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        return "task"


class _ContradictionAgent(ReasoningAgent):
    agent_id = "contradiction_agent"
    role = "Contradiction Agent"
    system_prompt = "test"

    async def _call_model(self, client, user_content, prior_response=None):
        return {
            "verdict": "alert",
            "risk_level": "low",
            "confidence": 0.84,
            "rationale": (
                "SIGNAL: routine arrival at front door. "
                "EVIDENCE: package handoff and resident-style behavior look benign. "
                "UNCERTAINTY: identity not confirmed. "
                "DECISION: suppress-like benign context but returning alert."
            ),
            "recommended_action": "keep in timeline only",
            "chain_notes": {},
        }, "ok", None

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        return "task"


def _make_packet() -> FramePacket:
    return FramePacket(
        frame_id="f1",
        stream_id="s1",
        timestamp=datetime.utcnow(),
        b64_frame="abc",
        stream_meta=StreamMeta(
            stream_id="s1",
            label="Cam 1",
            site_id="hq",
            zone="lobby",
            uri="direct",
        ),
        vision=VisionResult(
            threat=True,
            severity="medium",
            categories=["intrusion"],
            description="person crossing restricted line",
            confidence=0.92,
        ),
        history=HistoryContext(),
    )


class ReasoningFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_retry_repairs_invalid_output(self):
        agent = _RetryAgent()
        output = await agent.reason_draft(_make_packet(), client=None)
        self.assertEqual(output.verdict, "alert")
        self.assertGreaterEqual(agent.calls, 2)

    def test_truncated_json_repair_recovers_missing_tail_fields(self):
        broken = (
            '{"verdict":"suppress","risk_level":"low","confidence":0.62,'
            '"rationale":"SIGNAL: routine arrival EVIDENCE: direct approach with backpack '
            'UNCERTAINTY: identity unknown DECISION: suppress",'
            '"recommended_action":"continue monitoring",'
        )
        repaired = _repair_truncated_json(broken)
        self.assertIsNotNone(repaired)
        payload = json.loads(repaired)
        self.assertEqual(payload["verdict"], "suppress")
        self.assertEqual(payload["risk_level"], "low")
        self.assertEqual(payload["chain_notes"], {})

    def test_extract_json_content_prefers_final_reasoning_payload(self):
        raw = (
            "<think>Schema sketch {verdict:'alert', risk_level:'high'}</think>\n"
            '{"verdict":"suppress","risk_level":"low","confidence":0.62,'
            '"rationale":"SIGNAL: routine EVIDENCE: direct arrival UNCERTAINTY: identity unknown '
            'DECISION: suppress.",'
            '"recommended_action":"continue monitoring","chain_notes":{}}'
        )
        payload = json.loads(_extract_json_content(raw))
        self.assertEqual(payload["verdict"], "suppress")
        self.assertEqual(payload["risk_level"], "low")

    def test_extract_json_content_strips_qwen3_think_tags(self):
        # Qwen3 models output <think>...</think> blocks before JSON
        raw = (
            "<think>\n"
            "Okay, let's analyze this. The user wants me to respond with JSON.\n"
            "I need to consider the security context carefully.\n"
            "The person is at the front door at night, which could be suspicious.\n"
            "</think>\n"
            '{"verdict":"alert","risk_level":"medium","confidence":0.75,'
            '"rationale":"SIGNAL: entry approach EVIDENCE: person at door at night '
            'UNCERTAINTY: identity unknown DECISION: alert",'
            '"recommended_action":"review promptly","chain_notes":{}}'
        )
        payload = json.loads(_extract_json_content(raw))
        self.assertEqual(payload["verdict"], "alert")
        self.assertEqual(payload["risk_level"], "medium")
        self.assertEqual(payload["confidence"], 0.75)

    def test_extract_json_content_handles_incomplete_think_block(self):
        # Sometimes the model may not close the think tag properly
        raw = (
            "<think>Let me think about this security scenario... "
            "The person is approaching the door"
            '{"verdict":"suppress","risk_level":"low","confidence":0.8,'
            '"rationale":"SIGNAL: routine EVIDENCE: normal approach '
            'UNCERTAINTY: none DECISION: suppress",'
            '"recommended_action":"continue monitoring","chain_notes":{}}'
        )
        payload = json.loads(_extract_json_content(raw))
        self.assertEqual(payload["verdict"], "suppress")

    async def test_run_reasoning_runs_four_parallel_agents(self):
        peer_counts: list[tuple[str, int]] = []

        def _agent_cls(agent_id: str, role: str, verdict: str):
            class _Agent:
                chain_defaults = {}

                def __init__(self):
                    self.agent_id = agent_id
                    self.role = role

                async def reason_with_metrics(self, packet, client, peer_outputs=None):
                    from backend.models.schemas import AgentOutput

                    peer_counts.append((self.agent_id, len(peer_outputs or {})))
                    return AgentOutput(
                        agent_id=self.agent_id,
                        role=self.role,
                        verdict=verdict,
                        risk_level="high" if verdict == "alert" else "low",
                        confidence=1.0,
                        rationale=(
                            "SIGNAL: stable scene. EVIDENCE: aligned mock evidence. "
                            "UNCERTAINTY: none. DECISION: mock verdict."
                        ),
                        recommended_action="review promptly" if verdict == "alert" else "continue monitoring",
                        chain_notes={},
                    ), {"latency_ms": 5.0, "repair_count": 0, "model_calls": 1, "skipped": False}

            return _Agent

        with patch(
            "backend.agent.reasoning.context_baseline_reasoner.ContextBaselineReasonerAgent",
            _agent_cls("context_baseline_reasoner", "Context Baseline", "alert"),
        ), patch(
            "backend.agent.reasoning.trajectory_intent_assessor.TrajectoryIntentAssessorAgent",
            _agent_cls("trajectory_intent_assessor", "Trajectory Intent", "alert"),
        ), patch(
            "backend.agent.reasoning.falsification_auditor.FalsificationAuditorAgent",
            _agent_cls("falsification_auditor", "Falsification Auditor", "suppress"),
        ), patch(
            "backend.agent.reasoning.executive_triage_commander.ExecutiveTriageCommanderAgent",
            _agent_cls("executive_triage_commander", "Executive Triage", "suppress"),
        ):
            packet = _make_packet()
            bus = AgentMessageBus(
                [
                    "context_baseline_reasoner",
                    "trajectory_intent_assessor",
                    "falsification_auditor",
                    "executive_triage_commander",
                ]
            )
            verdict = await arbiter.run_reasoning(
                packet=packet,
                b64_thumbnail="thumb",
                bus=bus,
                client=None,
            )

        self.assertIn(verdict.routing.action, ("alert", "suppress"))
        # Phase 1: agents 1–3 run in parallel with empty peer_outputs; Phase 2: agent 4 gets all three
        self.assertEqual(
            dict(peer_counts),
            {
                "context_baseline_reasoner": 0,
                "trajectory_intent_assessor": 0,
                "falsification_auditor": 0,
                "executive_triage_commander": 3,
            },
        )
        self.assertEqual(verdict.telemetry["reasoning_agent_calls"], 4)
        self.assertEqual(verdict.telemetry["reasoning_rounds"], 2)
        self.assertEqual(verdict.telemetry["reasoning_invalid_output_count"], 0)
        self.assertEqual(verdict.telemetry["reasoning_local_repairs"], 0)
        self.assertEqual(verdict.telemetry["reasoning_fallback_agents"], 0)

    async def test_run_reasoning_runs_single_round_on_benign_consensus(self):
        def _agent_cls(agent_id: str, role: str):
            class _Agent:
                chain_defaults = {}

                def __init__(self):
                    self.agent_id = agent_id
                    self.role = role

                async def reason_with_metrics(self, packet, client, peer_outputs=None):
                    from backend.models.schemas import AgentOutput

                    return AgentOutput(
                        agent_id=self.agent_id,
                        role=self.role,
                        verdict="suppress",
                        risk_level="low",
                        confidence=0.9,
                        rationale=(
                            "SIGNAL: routine scene. EVIDENCE: calm low-risk activity. "
                            "UNCERTAINTY: none. DECISION: suppress."
                        ),
                        recommended_action="keep in timeline only",
                        chain_notes={},
                    ), {"latency_ms": 5.0, "repair_count": 0, "model_calls": 1, "skipped": False}

            return _Agent

        with patch(
            "backend.agent.reasoning.context_baseline_reasoner.ContextBaselineReasonerAgent",
            _agent_cls("context_baseline_reasoner", "Context Baseline"),
        ), patch(
            "backend.agent.reasoning.trajectory_intent_assessor.TrajectoryIntentAssessorAgent",
            _agent_cls("trajectory_intent_assessor", "Trajectory Intent"),
        ), patch(
            "backend.agent.reasoning.falsification_auditor.FalsificationAuditorAgent",
            _agent_cls("falsification_auditor", "Falsification Auditor"),
        ), patch(
            "backend.agent.reasoning.executive_triage_commander.ExecutiveTriageCommanderAgent",
            _agent_cls("executive_triage_commander", "Executive Triage"),
        ):
            packet = FramePacket(
                frame_id="f3",
                stream_id="s1",
                timestamp=datetime.utcnow(),
                b64_frame="abc",
                stream_meta=StreamMeta(
                    stream_id="s1",
                    label="Cam 1",
                    site_id="hq",
                    zone="driveway",
                    uri="direct",
                ),
                vision=VisionResult(
                    threat=False,
                    severity="none",
                    categories=["person"],
                    description="resident walking to driveway",
                    confidence=0.95,
                ),
                history=HistoryContext(),
            )
            bus = AgentMessageBus(
                [
                    "context_baseline_reasoner",
                    "trajectory_intent_assessor",
                    "falsification_auditor",
                    "executive_triage_commander",
                ]
            )
            verdict = await arbiter.run_reasoning(packet=packet, b64_thumbnail="thumb", bus=bus, client=None)

        self.assertEqual(verdict.telemetry["reasoning_agent_calls"], 4)
        self.assertEqual(verdict.telemetry["reasoning_rounds"], 2)

    async def test_prompt_drift_falls_back_to_uncertain(self):
        agent = _PromptDriftAgent()
        output = await agent.reason_draft(_make_packet(), client=None)
        self.assertEqual(output.verdict, "uncertain")
        self.assertIn("prompt_drift", output.rationale)

    async def test_low_evidence_high_confidence_falls_back_to_uncertain(self):
        agent = _LowEvidenceAgent()
        output = await agent.reason_draft(_make_packet(), client=None)
        self.assertEqual(output.verdict, "uncertain")
        self.assertIn("low_evidence", output.rationale)

    async def test_contradiction_falls_back_to_uncertain(self):
        agent = _ContradictionAgent()
        output = await agent.reason_draft(_make_packet(), client=None)
        self.assertEqual(output.verdict, "uncertain")
        self.assertIn("contradiction", output.rationale)

    def test_arbiter_adds_contradiction_and_basis_diagnostics_for_suppress(self):
        packet = FramePacket(
            frame_id="f2",
            stream_id="s1",
            timestamp=datetime.utcnow(),
            b64_frame="abc",
            stream_meta=StreamMeta(
                stream_id="s1",
                label="Cam 1",
                site_id="hq",
                zone="front_door",
                uri="direct",
            ),
            vision=VisionResult(
                threat=False,
                severity="none",
                categories=["person"],
                identity_labels=["person"],
                risk_labels=["clear"],
                description="resident entering with groceries",
                confidence=0.93,
            ),
            history=HistoryContext(),
        )
        outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="Context Baseline",
                verdict="alert",
                risk_level="high",
                confidence=0.95,
                rationale="SIGNAL: suspicious person. EVIDENCE: unknown face at door. UNCERTAINTY: identity unresolved. DECISION: alert.",
                recommended_action="review promptly",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="trajectory_intent_assessor",
                role="Trajectory Intent",
                verdict="alert",
                risk_level="medium",
                confidence=0.9,
                rationale="SIGNAL: repeated approach. EVIDENCE: pacing near entry. UNCERTAINTY: no package visible. DECISION: alert.",
                recommended_action="review promptly",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="falsification_auditor",
                role="Falsification Auditor",
                verdict="suppress",
                risk_level="low",
                confidence=0.2,
                rationale="SIGNAL: known daytime routine. EVIDENCE: predictable timing. UNCERTAINTY: face partially obscured. DECISION: suppress.",
                recommended_action="keep in timeline only",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="executive_triage_commander",
                role="Executive Triage",
                verdict="alert",
                risk_level="medium",
                confidence=0.85,
                rationale="SIGNAL: benign household behavior. EVIDENCE: grocery bag and familiar path. UNCERTAINTY: none. DECISION: alert.",
                recommended_action="review promptly",
                chain_notes={},
            ),
        ]

        verdict = arbiter._compute_verdict(packet, outputs, "thumb")

        reasoning = verdict.audit.liability_digest.decision_reasoning
        self.assertEqual(verdict.routing.action, "suppress")
        self.assertIn("SUPPRESS_BASIS:", reasoning)
        self.assertIn("ALERT_BASIS:", reasoning)
        self.assertIn("CONFIDENCE_DECOMPOSITION:", reasoning)
        self.assertIn("CONSISTENCY_CHECKS:", reasoning)
        self.assertIn("warn: alert-leaning votes conflict with non-threat vision semantics", reasoning)
        self.assertIn("threat_semantics_unmet", reasoning)

    def test_arbiter_adds_confidence_decomposition_for_alert(self):
        packet = _make_packet()
        outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="Context Baseline",
                verdict="alert",
                risk_level="high",
                confidence=0.95,
                rationale="SIGNAL: perimeter breach. EVIDENCE: restricted line crossed. UNCERTAINTY: intent unknown. DECISION: alert.",
                recommended_action="notify immediately",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="trajectory_intent_assessor",
                role="Trajectory Intent",
                verdict="alert",
                risk_level="high",
                confidence=0.9,
                rationale="SIGNAL: direct approach. EVIDENCE: target-focused movement. UNCERTAINTY: no tool visibility. DECISION: alert.",
                recommended_action="review promptly",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="falsification_auditor",
                role="Falsification Auditor",
                verdict="suppress",
                risk_level="low",
                confidence=0.2,
                rationale="SIGNAL: low-value target. EVIDENCE: no forced entry signs. UNCERTAINTY: cannot verify duration. DECISION: suppress.",
                recommended_action="continue monitoring",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="executive_triage_commander",
                role="Executive Triage",
                verdict="suppress",
                risk_level="low",
                confidence=0.1,
                rationale="SIGNAL: possible resident behavior. EVIDENCE: calm pace. UNCERTAINTY: identity not confirmed. DECISION: suppress.",
                recommended_action="keep in timeline only",
                chain_notes={},
            ),
        ]

        verdict = arbiter._compute_verdict(packet, outputs, "thumb")

        reasoning = verdict.audit.liability_digest.decision_reasoning
        self.assertEqual(verdict.routing.action, "alert")
        self.assertIn("ALERT_BASIS:", reasoning)
        self.assertIn("SUPPRESS_BASIS:", reasoning)
        self.assertIn("selected=", reasoning)
        self.assertIn("contributions=[", reasoning)
        self.assertIn("context_baseline_reasoner:alert", reasoning)

    def test_compute_verdict_preserves_vision_severity_in_routing(self):
        packet = _make_packet()
        packet.vision.severity = "medium"
        outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="Context Baseline",
                verdict="alert",
                risk_level="high",
                confidence=0.95,
                rationale="SIGNAL: intrusion cue. EVIDENCE: line crossed. UNCERTAINTY: intent unknown. DECISION: alert.",
                recommended_action="notify immediately",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="trajectory_intent_assessor",
                role="Trajectory Intent",
                verdict="alert",
                risk_level="high",
                confidence=0.91,
                rationale="SIGNAL: direct approach. EVIDENCE: targeted movement. UNCERTAINTY: tool not visible. DECISION: alert.",
                recommended_action="review promptly",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="falsification_auditor",
                role="Falsification Auditor",
                verdict="suppress",
                risk_level="low",
                confidence=0.2,
                rationale="SIGNAL: limited benign cues. EVIDENCE: no package seen. UNCERTAINTY: duration unknown. DECISION: suppress.",
                recommended_action="continue monitoring",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="executive_triage_commander",
                role="Executive Triage",
                verdict="alert",
                risk_level="high",
                confidence=0.88,
                rationale="SIGNAL: strong threat pattern. EVIDENCE: aligned entry-risk cues. UNCERTAINTY: identity unresolved. DECISION: alert.",
                recommended_action="notify immediately",
                chain_notes={},
            ),
        ]

        verdict = arbiter._compute_verdict(packet, outputs, "thumb")

        self.assertEqual(verdict.routing.action, "alert")
        self.assertEqual(verdict.routing.risk_level, "high")
        self.assertEqual(verdict.routing.severity, "medium")

    def test_arbiter_prefers_executive_triage_summary_fields(self):
        packet = _make_packet()
        outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="Context Baseline",
                verdict="alert",
                risk_level="high",
                confidence=0.91,
                rationale="SIGNAL: anomaly. EVIDENCE: late activity. UNCERTAINTY: identity unknown. DECISION: alert.",
                recommended_action="review promptly",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="trajectory_intent_assessor",
                role="Trajectory Intent",
                verdict="uncertain",
                risk_level="medium",
                confidence=0.55,
                rationale="SIGNAL: mixed behavior. EVIDENCE: approach without contact. UNCERTAINTY: no follow-through. DECISION: uncertain.",
                recommended_action="continue monitoring",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="falsification_auditor",
                role="Falsification Auditor",
                verdict="suppress",
                risk_level="low",
                confidence=0.3,
                rationale="SIGNAL: benign option. EVIDENCE: no tamper shown. UNCERTAINTY: limited frame. DECISION: suppress.",
                recommended_action="keep in timeline only",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="executive_triage_commander",
                role="Executive Triage",
                verdict="alert",
                risk_level="medium",
                confidence=0.82,
                rationale="SIGNAL: unresolved entry concern. EVIDENCE: alerting cues outweigh benign support. UNCERTAINTY: limited single-frame context. DECISION: alert.",
                recommended_action="review promptly",
                consumer_headline="Review entry activity",
                consumer_reason="Visible activity near the entry needs a quick check.",
                operator_observed="Observed person near entry zone with unresolved context.",
                operator_triage="Final triage favored alert because entry-risk cues outweighed benign explanations.",
                chain_notes={},
            ),
        ]

        verdict = arbiter._compute_verdict(packet, outputs, "thumb")

        self.assertEqual(verdict.summary.headline, "Review entry activity")
        self.assertIn("Observed person near entry zone", verdict.summary.narrative)
        self.assertEqual(verdict.consumer_summary.headline, "Review entry activity")
        self.assertEqual(
            verdict.operator_summary.why_flagged,
            "Final triage favored alert because entry-risk cues outweighed benign explanations.",
        )

    def test_vision_agent_view_exposes_grounded_fields_for_behavior_agent(self):
        packet = FramePacket(
            frame_id="f-observed-1",
            stream_id="s1",
            timestamp=datetime.utcnow(),
            b64_frame="abc",
            stream_meta=StreamMeta(
                stream_id="s1",
                label="Front Door",
                site_id="hq",
                zone="front_door",
                uri="direct",
            ),
            vision=VisionResult(
                setting="porch_door",
                observed_entities=["person"],
                observed_actions=["approaching_entry", "carrying_package"],
                spatial_tags=["at_entry"],
                object_labels=["package"],
                visibility_tags=["clear_view"],
                evidence_notes=["person on walkway", "package in hand"],
                threat=False,
                severity="none",
                categories=["person", "package"],
                identity_labels=["person"],
                risk_labels=["delivery_pattern"],
                description="person approaching front door with package",
                confidence=0.91,
            ),
            history=HistoryContext(),
        )

        view = _vision_agent_view(packet, "trajectory_intent_assessor")

        self.assertIn("OBS:", view)
        self.assertIn("actions=approaching_entry,carrying_package", view)
        self.assertIn("objects=package", view)
        self.assertIn("QUAL:", view)
        self.assertIn("do not infer psychology or gaze", view)

    def test_summary_uses_separate_identity_and_risk_wording(self):
        packet = FramePacket(
            frame_id="f3",
            stream_id="s1",
            timestamp=datetime.utcnow(),
            b64_frame="abc",
            stream_meta=StreamMeta(
                stream_id="s1",
                label="Cam 1",
                site_id="hq",
                zone="backyard",
                uri="direct",
            ),
            vision=VisionResult(
                threat=True,
                severity="medium",
                categories=["person", "intrusion"],
                identity_labels=["person"],
                risk_labels=["intrusion"],
                description="person climbing over side gate",
                confidence=0.95,
            ),
            history=HistoryContext(),
        )
        outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="Context Baseline",
                verdict="alert",
                confidence=0.9,
                rationale="SIGNAL: intrusion cue. EVIDENCE: gate crossing. UNCERTAINTY: identity unknown. DECISION: alert.",
                chain_notes={},
            )
        ]

        verdict = arbiter._compute_verdict(packet, outputs, "thumb")

        self.assertIn("security risk", verdict.summary.headline.lower())
        self.assertIn("observed person", verdict.summary.headline.lower())
        self.assertIn("Activity identity:", verdict.summary.narrative)
        self.assertIn("Security interpretation:", verdict.summary.narrative)

    def test_wildlife_scenario_keeps_identity_separate_from_risk_and_emits_diagnostics(self):
        packet = FramePacket(
            frame_id="f4",
            stream_id="s1",
            timestamp=datetime.utcnow(),
            b64_frame="abc",
            stream_meta=StreamMeta(
                stream_id="s1",
                label="Cam 1",
                site_id="hq",
                zone="backyard",
                uri="direct",
            ),
            vision=VisionResult(
                threat=False,
                severity="none",
                categories=["pet"],
                identity_labels=["wildlife"],
                risk_labels=["clear"],
                description="deer moving near fence line",
                confidence=0.87,
            ),
            history=HistoryContext(),
        )
        outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="Threat Escalation",
                verdict="alert",
                confidence=0.92,
                rationale="SIGNAL: movement near perimeter. EVIDENCE: motion by fence. UNCERTAINTY: species not fully confirmed. DECISION: alert.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="trajectory_intent_assessor",
                role="Behavioral Pattern",
                verdict="suppress",
                confidence=0.7,
                rationale="SIGNAL: non-human gait. EVIDENCE: animal movement pattern. UNCERTAINTY: low light. DECISION: suppress.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="falsification_auditor",
                role="Context Risk",
                verdict="suppress",
                confidence=0.8,
                rationale="SIGNAL: low asset risk. EVIDENCE: open yard activity. UNCERTAINTY: none. DECISION: suppress.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="executive_triage_commander",
                role="Adversarial Challenger",
                verdict="suppress",
                confidence=0.75,
                rationale="SIGNAL: benign wildlife explanation. EVIDENCE: deer silhouette and trajectory. UNCERTAINTY: none. DECISION: suppress.",
                chain_notes={},
            ),
        ]

        verdict = arbiter._compute_verdict(packet, outputs, "thumb")

        reasoning = verdict.audit.liability_digest.decision_reasoning
        headline = verdict.summary.headline.lower()
        self.assertEqual(verdict.routing.action, "suppress")
        self.assertIn("observed wildlife", headline)
        self.assertNotIn("observed possible intrusion", headline)
        self.assertIn("CONFIDENCE_DECOMPOSITION:", reasoning)
        self.assertIn("CONSISTENCY_CHECKS:", reasoning)
        self.assertIn("threat_semantics_unmet", reasoning)

    def test_ambiguous_identity_scenario_surfaces_uncertainty_without_semantic_mixing(self):
        packet = FramePacket(
            frame_id="f5",
            stream_id="s1",
            timestamp=datetime.utcnow(),
            b64_frame="abc",
            stream_meta=StreamMeta(
                stream_id="s1",
                label="Cam 1",
                site_id="hq",
                zone="side_gate",
                uri="direct",
            ),
            vision=VisionResult(
                threat=False,
                severity="none",
                categories=["motion"],
                identity_labels=["unknown"],
                risk_labels=["motion"],
                description="unclear figure near side gate",
                confidence=0.58,
            ),
            history=HistoryContext(),
        )
        outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="Threat Escalation",
                verdict="uncertain",
                confidence=0.45,
                rationale="SIGNAL: possible approach. EVIDENCE: partial silhouette. UNCERTAINTY: identity unresolved. DECISION: uncertain.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="trajectory_intent_assessor",
                role="Behavioral Pattern",
                verdict="suppress",
                confidence=0.65,
                rationale="SIGNAL: no hostile trajectory. EVIDENCE: brief pass-through motion. UNCERTAINTY: limited angle. DECISION: suppress.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="falsification_auditor",
                role="Context Risk",
                verdict="suppress",
                confidence=0.55,
                rationale="SIGNAL: low-risk context. EVIDENCE: no forced-entry indicator. UNCERTAINTY: no identity confirmation. DECISION: suppress.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="executive_triage_commander",
                role="Adversarial Challenger",
                verdict="uncertain",
                confidence=0.4,
                rationale="SIGNAL: plausible benign passerby. EVIDENCE: short dwell time. UNCERTAINTY: identity unresolved. DECISION: uncertain.",
                chain_notes={},
            ),
        ]

        verdict = arbiter._compute_verdict(packet, outputs, "thumb")

        reasoning = verdict.audit.liability_digest.decision_reasoning
        headline = verdict.summary.headline.lower()
        narrative = verdict.summary.narrative
        self.assertEqual(verdict.routing.action, "suppress")
        self.assertIn("observed unknown", headline)
        self.assertIn("low home-security risk (motion detected)", headline)
        self.assertIn("• Agent consensus:", narrative)
        self.assertIn("ambiguity remains and monitoring continues", narrative)
        self.assertIn("CONFIDENCE_DECOMPOSITION:", reasoning)
        self.assertIn("CONSISTENCY_CHECKS:", reasoning)
        self.assertIn("threat_semantics_unmet", reasoning)

    def test_clear_threat_scenario_preserves_identity_and_risk_and_emits_alert_diagnostics(self):
        packet = FramePacket(
            frame_id="f6",
            stream_id="s1",
            timestamp=datetime.utcnow(),
            b64_frame="abc",
            stream_meta=StreamMeta(
                stream_id="s1",
                label="Cam 1",
                site_id="hq",
                zone="front_door",
                uri="direct",
            ),
            vision=VisionResult(
                threat=True,
                severity="high",
                categories=["person", "intrusion"],
                identity_labels=["person"],
                risk_labels=["intrusion"],
                description="unknown person forcing front door lock",
                confidence=0.96,
            ),
            history=HistoryContext(),
        )
        outputs = [
            AgentOutput(
                agent_id="context_baseline_reasoner",
                role="Threat Escalation",
                verdict="alert",
                confidence=0.96,
                rationale="SIGNAL: forced-entry signal. EVIDENCE: lock tampering at entry. UNCERTAINTY: intent unknown but hostile indicators present. DECISION: alert.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="trajectory_intent_assessor",
                role="Behavioral Pattern",
                verdict="alert",
                confidence=0.9,
                rationale="SIGNAL: target-focused intrusion behavior. EVIDENCE: repeated forceful contact with lock. UNCERTAINTY: no tool classification. DECISION: alert.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="falsification_auditor",
                role="Context Risk",
                verdict="suppress",
                confidence=0.2,
                rationale="SIGNAL: isolated event. EVIDENCE: no confirmed entry yet. UNCERTAINTY: duration unknown. DECISION: suppress.",
                chain_notes={},
            ),
            AgentOutput(
                agent_id="executive_triage_commander",
                role="Adversarial Challenger",
                verdict="suppress",
                confidence=0.1,
                rationale="SIGNAL: weak benign hypothesis. EVIDENCE: could be resident lock issue. UNCERTAINTY: low support for benign interpretation. DECISION: suppress.",
                chain_notes={},
            ),
        ]

        verdict = arbiter._compute_verdict(packet, outputs, "thumb")

        reasoning = verdict.audit.liability_digest.decision_reasoning
        headline = verdict.summary.headline.lower()
        self.assertEqual(verdict.routing.action, "alert")
        self.assertIn("observed person", headline)
        self.assertIn("security risk (possible intrusion)", headline)
        self.assertNotIn("observed possible intrusion", headline)
        self.assertIn("ALERT_BASIS:", reasoning)
        self.assertIn("SUPPRESS_BASIS:", reasoning)
        self.assertIn("CONFIDENCE_DECOMPOSITION:", reasoning)
        self.assertIn("CONSISTENCY_CHECKS:", reasoning)
        self.assertIn("threat_semantics_met", reasoning)


if __name__ == "__main__":
    unittest.main()
