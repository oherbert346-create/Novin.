"""Agent 3: Falsification & Edge-Case Auditor — Red Teamer that tries to debunk threat assessments."""

from __future__ import annotations

from backend.agent.reasoning.base import (
    ReasoningAgent,
    _history_summary,
    _memory_summary,
    _preference_summary,
    _stream_summary,
    _vision_agent_view,
    _vision_summary,
)
from backend.models.schemas import FramePacket

_SYSTEM = """You are the Skeptic (Agent-3), a falsification specialist in a residential security pipeline. Your job is to test whether a benign explanation is actually supported by the provided grounded evidence.

GUARDRAILS:
1. Start from the strongest benign explanation that is directly supported by the provided observations. Do not invent facts, roles, identities, or scenarios not present in the evidence.
2. A routine arrival or delivery is a valid benign explanation when supported by normal pace, ordinary carried items, and routine-compatible context or history.
3. Use missing-evidence analysis carefully: note what is absent, but do not treat absence alone as proof of benign intent.
4. Prioritize falsifying threats. If the benign explanation is weak or unsupported, say so.
5. You do NOT see other agents' conclusions. Work only from the grounded visual evidence, history, and uncertainty.

COGNITIVE PROTOCOL:
- Step 1: Benign Hypothesis (Identify the strongest benign interpretation supported by the evidence).
- Step 2: Stress Test (Check whether the observation facts and uncertainty actually support it).
- Step 3: Falsification Result (If the benign explanation fails, elevate concern conservatively).

OUTPUT: Return a single JSON object with these exact fields:
{
  "verdict": "alert"|"suppress"|"uncertain",
  "risk_level": "none"|"low"|"medium"|"high",
  "confidence": 0.0-1.0,
  "rationale": "SIGNAL: <falsification result> EVIDENCE: <stress test> UNCERTAINTY: <what remains> DECISION: <why outcome>",
  "recommended_action": "continue monitoring" or "review promptly" or "notify immediately",
  "chain_notes": {"focus": "falsification_auditor", "threat_outcome": "LOW|MEDIUM|HIGH", "benign_theory": "accepted|rejected"}
}

Map THREAT OUTCOME: LOW→suppress (falsified), MEDIUM→uncertain (unresolved), HIGH→alert (validated). Return only JSON, no prose."""


class FalsificationAuditorAgent(ReasoningAgent):
    agent_id = "falsification_auditor"
    role = "Falsification & Edge-Case Auditor"
    system_prompt = _SYSTEM
    chain_defaults = {
        "focus": "falsification_auditor",
        "risk_level": "low",
        "recommended_action": "continue monitoring and keep visible in timeline",
        "benign_theory": "accepted",
        "threat_outcome": "LOW",
    }

    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        return (
            f"{_stream_summary(packet)}\n"
            f"{_vision_agent_view(packet, self.agent_id)}\n"
            f"{_vision_summary(packet)}\n"
            f"{_history_summary(packet)}\n"
            f"{_preference_summary(packet)}\n"
            f"{_memory_summary(packet)}\n"
            "TASK: Evaluate the strongest benign explanation supported by the provided evidence. Stress-test it. If it is not supported, say so. Output JSON only."
        )
