import pytest
from unittest.mock import AsyncMock, patch
from backend.agent.reasoning.falsification_auditor import FalsificationAuditorAgent
from backend.agent.reasoning.trajectory_intent_assessor import TrajectoryIntentAssessorAgent
from backend.models.schemas import FramePacket, StreamMeta, VisionResult, HistoryContext

@pytest.fixture
def mock_packet():
    return FramePacket(
        frame_id="test_id",
        stream_id="test_cam",
        timestamp=pytest.importorskip("datetime").datetime.utcnow(),
        b64_frame="test_b64",
        stream_meta=StreamMeta(stream_id="test_cam", label="cam", site_id="home", zone="front_door", uri="test"),
        vision=VisionResult(
            threat=True,
            severity="medium",
            categories=["person"],
            description="person detected",
            confidence=0.8
        ),
        history=HistoryContext()
    )

@pytest.mark.asyncio
async def test_falsification_auditor_agent(mock_packet):
    agent = FalsificationAuditorAgent()
    with patch.object(agent, "_call_model", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = (
            {
                "verdict": "suppress",
                "risk_level": "low",
                "confidence": 0.9,
                "rationale": (
                    "SIGNAL: routine doorstep interaction. "
                    "EVIDENCE: subject leaves package and exits with calm behavior. "
                    "UNCERTAINTY: face identity is not confirmed. "
                    "DECISION: suppress because evidence supports delivery scenario."
                ),
                "recommended_action": "ignore",
                "chain_notes": {"benign_theory": "accepted"},
            },
            '{"verdict": "suppress"}',
            None,
            0.0,
        )

        output = await agent.reason_draft(mock_packet, AsyncMock())
        assert output.verdict == "suppress"
        assert output.chain_notes.get("benign_theory") == "accepted"

@pytest.mark.asyncio
async def test_trajectory_intent_assessor_agent(mock_packet):
    agent = TrajectoryIntentAssessorAgent()
    with patch.object(agent, "_call_model", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = (
            {
                "verdict": "alert",
                "risk_level": "high",
                "confidence": 0.8,
                "rationale": (
                    "SIGNAL: escalating movement near entry point. "
                    "EVIDENCE: person repeatedly approaches and lingers by front door at night. "
                    "UNCERTAINTY: tool possession is not visible. "
                    "DECISION: alert because observed behavior matches intrusion-like pattern."
                ),
                "recommended_action": "notify immediately",
                "chain_notes": {},
            },
            '{"verdict": "alert"}',
            None,
            0.0,
        )

        output = await agent.reason_draft(mock_packet, AsyncMock())
        assert output.verdict == "alert"
        assert output.confidence == 0.8
