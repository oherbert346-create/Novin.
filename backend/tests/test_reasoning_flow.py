from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from backend.agent.bus import AgentMessageBus
from backend.agent.reasoning import arbiter
from backend.agent.reasoning.base import ReasoningAgent
from backend.models.schemas import FramePacket, HistoryContext, StreamMeta, VisionResult


class _RetryAgent(ReasoningAgent):
    agent_id = "retry_agent"
    role = "Retry Agent"
    system_prompt = "test"

    def __init__(self) -> None:
        self.calls = 0

    async def _call_model(self, client, user_content, prior_response=None):
        self.calls += 1
        if self.calls == 1:
            return {"verdict": "invalid", "confidence": 0.5, "rationale": "bad", "chain_notes": {}}, "bad", None
        return {"verdict": "alert", "confidence": 0.8, "rationale": "repaired", "chain_notes": {}}, "ok", None

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

    async def test_run_reasoning_finalize_receives_peer_outputs(self):
        finalize_peer_counts: list[int] = []

        def _agent_cls(agent_id: str, role: str, verdict: str):
            class _Agent:
                chain_defaults = {}

                def __init__(self):
                    self.agent_id = agent_id
                    self.role = role

                async def reason_draft(self, packet, client):
                    from backend.models.schemas import AgentOutput

                    return AgentOutput(
                        agent_id=self.agent_id,
                        role=self.role,
                        verdict=verdict,
                        confidence=1.0,
                        rationale="draft",
                        chain_notes={},
                    )

                async def reason_finalize(self, packet, peer_outputs, client):
                    from backend.models.schemas import AgentOutput

                    finalize_peer_counts.append(len(peer_outputs))
                    return AgentOutput(
                        agent_id=self.agent_id,
                        role=self.role,
                        verdict=verdict,
                        confidence=1.0,
                        rationale="final",
                        chain_notes={},
                    )

            return _Agent

        with patch(
            "backend.agent.reasoning.threat_escalation.ThreatEscalationAgent",
            _agent_cls("threat_escalation", "Threat", "alert"),
        ), patch(
            "backend.agent.reasoning.behavioural_pattern.BehaviouralPatternAgent",
            _agent_cls("behavioural_pattern", "Behaviour", "alert"),
        ), patch(
            "backend.agent.reasoning.context_asset_risk.ContextAssetRiskAgent",
            _agent_cls("context_asset_risk", "Context", "suppress"),
        ), patch(
            "backend.agent.reasoning.adversarial_challenger.AdversarialChallengerAgent",
            _agent_cls("adversarial_challenger", "Challenger", "suppress"),
        ):
            packet = _make_packet()
            bus = AgentMessageBus(
                [
                    "threat_escalation",
                    "behavioural_pattern",
                    "context_asset_risk",
                    "adversarial_challenger",
                ]
            )
            verdict = await arbiter.run_reasoning(
                packet=packet,
                b64_thumbnail="thumb",
                bus=bus,
                client=None,
            )

        self.assertEqual(verdict.routing.action, "alert")
        self.assertEqual(finalize_peer_counts, [3, 3, 3, 3])


if __name__ == "__main__":
    unittest.main()
