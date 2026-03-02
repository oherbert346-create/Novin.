from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.agent.bus import AgentMessageBus
from backend.agent.event_narrator import SecurityEventNarrator
from backend.models.schemas import AgentOutput, FramePacket, Verdict

logger = logging.getLogger(__name__)

# Weights must sum to 1.0
_WEIGHTS = {
    "threat_escalation": 0.30,
    "behavioural_pattern": 0.25,
    "context_asset_risk": 0.25,
    "adversarial_challenger": 0.20,
}

# Severity gate: only alert if vision severity is at least this level
_SEVERITY_ORDER = ["none", "low", "medium", "high", "critical"]
_MIN_SEVERITY_TO_ALERT = "low"

# Confidence threshold above which we alert
_ALERT_THRESHOLD = 0.55


def _severity_index(s: str) -> int:
    try:
        return _SEVERITY_ORDER.index(s)
    except ValueError:
        return 0


def _compute_verdict(
    packet: FramePacket,
    agent_outputs: list[AgentOutput],
    b64_thumbnail: str,
) -> Verdict:
    alert_score = 0.0
    suppress_score = 0.0

    for output in agent_outputs:
        weight = _WEIGHTS.get(output.agent_id, 0.0)
        if output.verdict == "alert":
            alert_score += weight * output.confidence
        elif output.verdict == "suppress":
            suppress_score += weight * output.confidence
        # uncertain contributes 0 to both

    total = alert_score + suppress_score
    final_confidence = alert_score / total if total > 0 else 0.0

    severity_ok = _severity_index(packet.vision.severity) >= _severity_index(_MIN_SEVERITY_TO_ALERT)
    should_alert = final_confidence >= _ALERT_THRESHOLD and severity_ok and packet.vision.threat

    action = "alert" if should_alert else "suppress"

    # Generate operator-facing summary text
    narrator = SecurityEventNarrator()
    summary = narrator.generate_headline(
        packet=packet,
        action=action,
        final_confidence=final_confidence,
    )
    narrative_summary = narrator.generate_narrative(
        packet=packet,
        agent_outputs=agent_outputs,
        action=action,
        final_confidence=final_confidence,
    )
    
    # Build traditional summary for backward compatibility
    alert_agent = next((o for o in agent_outputs if o.agent_id == "threat_escalation"), None)
    challenger = next((o for o in agent_outputs if o.agent_id == "adversarial_challenger"), None)

    if should_alert:
        alert_reason = (
            f"Consensus confidence {final_confidence:.0%}. "
            + (alert_agent.rationale[:200] if alert_agent else "")
        )
        suppress_reason = None
    else:
        suppress_reason = (
            f"Confidence {final_confidence:.0%} below threshold or no credible threat. "
            + (challenger.rationale[:200] if challenger else "")
        )
        alert_reason = None

    from backend.models.schemas import MachineRouting, OperatorSummary, LiabilityDigest, AuditTrail
    
    routing = MachineRouting(
        is_threat=packet.vision.threat,
        action=action,
        severity=packet.vision.severity,
        categories=packet.vision.categories,
    )
    
    summary_obj = OperatorSummary(
        headline=summary,
        narrative=narrative_summary,
    )

    liability = LiabilityDigest(
        decision_reasoning=alert_reason if should_alert else suppress_reason,
        confidence_score=round(final_confidence, 4),
    )
    
    audit = AuditTrail(
        liability_digest=liability,
        agent_outputs=agent_outputs,
    )

    return Verdict(
        frame_id=packet.frame_id,
        stream_id=packet.stream_id,
        timestamp=packet.timestamp,
        routing=routing,
        summary=summary_obj,
        audit=audit,
        description=packet.vision.description,
        bbox=packet.vision.bbox,
        b64_thumbnail=b64_thumbnail,
    )


async def run_reasoning(
    packet: FramePacket,
    b64_thumbnail: str,
    bus: AgentMessageBus,
    client,
) -> Verdict:
    from backend.agent.reasoning.threat_escalation import ThreatEscalationAgent
    from backend.agent.reasoning.behavioural_pattern import BehaviouralPatternAgent
    from backend.agent.reasoning.context_asset_risk import ContextAssetRiskAgent
    from backend.agent.reasoning.adversarial_challenger import AdversarialChallengerAgent

    agent1 = ThreatEscalationAgent()
    agent2 = BehaviouralPatternAgent()
    agent3 = ContextAssetRiskAgent()
    agent4 = AdversarialChallengerAgent()
    agents = [agent1, agent2, agent3, agent4]

    draft_outputs = await asyncio.gather(
        *[_run_draft(agent, packet, client) for agent in agents],
    )
    for agent, draft in zip(agents, draft_outputs):
        await bus.publish(agent.agent_id, draft)

    await bus.wait_for_all()

    final_outputs = await asyncio.gather(
        *[
            _run_finalize(
                agent=agent,
                packet=packet,
                peer_outputs=bus.get_published(agent.agent_id),
                client=client,
            )
            for agent in agents
        ],
    )

    agent_outputs = list(final_outputs)

    verdict = _compute_verdict(packet, agent_outputs, b64_thumbnail)
    logger.info(
        "Verdict: %s | confidence=%.2f | frame=%s",
        verdict.routing.action,
        verdict.audit.liability_digest.confidence_score,
        packet.frame_id,
    )
    return verdict


async def _run_draft(agent, packet: FramePacket, client) -> AgentOutput:
    try:
        return await agent.reason_draft(packet, client)
    except Exception as exc:
        logger.error("Draft phase failed for %s: %s", agent.agent_id, exc)
        return _fallback_output(agent, f"draft_failed: {exc}")


async def _run_finalize(
    agent,
    packet: FramePacket,
    peer_outputs: dict[str, Any],
    client,
) -> AgentOutput:
    try:
        return await agent.reason_finalize(packet, peer_outputs, client)
    except Exception as exc:
        logger.error("Finalize phase failed for %s: %s", agent.agent_id, exc)
        return _fallback_output(agent, f"finalize_failed: {exc}")


def _fallback_output(agent, reason: str) -> AgentOutput:
    return AgentOutput(
        agent_id=agent.agent_id,
        role=agent.role,
        verdict="uncertain",
        confidence=0.0,
        rationale=f"Agent fallback: {reason}"[:280],
        chain_notes=getattr(agent, "chain_defaults", {}),
    )
