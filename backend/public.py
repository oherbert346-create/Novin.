from __future__ import annotations

from typing import Any

from backend.actions import build_action_bus_payload
from backend.agent.hallucination_guard import strip_capability_claims
from backend.models.schemas import Verdict
from backend.runtime import benchmark_enabled


def is_reasoning_degraded(verdict: Verdict) -> bool:
    return any(output.rationale.startswith("Agent fallback:") for output in verdict.audit.agent_outputs)


def public_agent_output(output) -> dict[str, Any]:
    return {
        "agent_id": output.agent_id,
        "role": output.role,
        "verdict": output.verdict,
        "rationale": strip_capability_claims(output.rationale),
        "chain_notes": output.chain_notes,
    }


def _first_sentence(text: str, fallback: str) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return fallback
    for separator in (". ", "; ", "\n"):
        if separator in clean:
            return clean.split(separator, 1)[0].strip().rstrip(".")
    return clean[:180].rstrip()


def _derive_case_status(verdict: Verdict) -> str:
    decision_text = " ".join(
        [
            verdict.description or "",
            verdict.summary.headline or "",
            verdict.audit.liability_digest.decision_reasoning or "",
        ]
    ).lower()
    if verdict.routing.action == "alert":
        if any(token in decision_text for token in ("forced", "tamper", "break", "intrusion", "weapon")):
            return "active_threat"
        if verdict.routing.risk_level == "high":
            return "urgent"
        if verdict.routing.risk_level == "medium":
            return "verify"
        return "watch"
    if verdict.routing.risk_level == "medium":
        return "verify"
    if verdict.routing.risk_level == "low":
        return "watch"
    return "closed_benign" if verdict.routing.action == "suppress" else "routine"


def _derive_ambiguity_state(verdict: Verdict) -> str:
    decision_text = " ".join(
        [
            verdict.description or "",
            verdict.summary.narrative or "",
            verdict.audit.liability_digest.decision_reasoning or "",
        ]
    ).lower()
    if any(output.verdict == "uncertain" for output in verdict.audit.agent_outputs):
        return "ambiguous"
    if "uncertain" in decision_text or "ambiguous" in decision_text:
        return "ambiguous"
    if verdict.event_context and verdict.event_context.metadata:
        return "monitoring"
    return "resolved"


def _derive_confidence_band(verdict: Verdict) -> str:
    confidence = float(verdict.audit.liability_digest.confidence_score or 0.0)
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.45:
        return "medium"
    return "low"


def _derive_next_action(case_status: str) -> str:
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


def _derive_delivery_targets(verdict: Verdict, case_status: str, ambiguity_state: str) -> list[str]:
    if verdict.routing.notification_policy == "immediate":
        return ["homeowner_app", "operator_queue", "webhook", "monitoring"]
    if verdict.routing.notification_policy == "review":
        return ["homeowner_app", "operator_queue", "timeline"]
    if case_status in {"watch", "interesting"}:
        targets = ["timeline"]
        if ambiguity_state in {"ambiguous", "contested"}:
            targets.append("operator_queue")
        return targets
    if verdict.routing.visibility_policy == "prominent":
        return ["homeowner_app", "timeline"]
    return ["timeline"]


def public_case_fields(verdict: Verdict) -> dict[str, Any]:
    metadata = verdict.event_context.metadata if verdict.event_context else {}
    case_id = (
        (verdict.case_id or "").strip()
        or (verdict.case.case_id or "").strip()
        or str(metadata.get("scenario_id", "")).strip()
        or str(metadata.get("case_id", "")).strip()
        or (verdict.event_context.source_event_id if verdict.event_context and verdict.event_context.source_event_id else "")
        or verdict.event_id
        or verdict.frame_id
    )
    case_status = verdict.case_status if verdict.case_status != "routine" or verdict.case.case_status == "routine" else verdict.case.case_status
    if case_status == "routine" and (
        verdict.routing.action == "alert"
        or verdict.routing.risk_level != "none"
        or any(category != "clear" for category in verdict.routing.categories)
    ):
        case_status = _derive_case_status(verdict)

    ambiguity_state = verdict.ambiguity_state
    if ambiguity_state == "resolved" and any(output.verdict == "uncertain" for output in verdict.audit.agent_outputs):
        ambiguity_state = _derive_ambiguity_state(verdict)

    confidence_band = verdict.confidence_band
    if confidence_band == "low" and float(verdict.audit.liability_digest.confidence_score or 0.0) >= 0.45:
        confidence_band = _derive_confidence_band(verdict)

    recommended_next_action = verdict.recommended_next_action or verdict.case.recommended_next_action or _derive_next_action(case_status)
    recommended_delivery_targets = (
        verdict.recommended_delivery_targets
        or verdict.case.recommended_delivery_targets
        or _derive_delivery_targets(verdict, case_status, ambiguity_state)
    )

    consumer_summary = verdict.consumer_summary.model_dump()
    if not any(consumer_summary.values()):
        consumer_summary = {
            "headline": (verdict.summary.headline or "Security activity")[:80],
            "reason": _first_sentence(verdict.summary.narrative or verdict.description, "Legacy compatibility summary.")[:90],
            "action_now": recommended_next_action.split(".", 1)[0][:90],
        }

    operator_summary = verdict.operator_summary.model_dump()
    if not any(operator_summary.values()):
        operator_summary = {
            "what_observed": _first_sentence(verdict.description, "Legacy verdict path without structured observation evidence.")[:220],
            "why_flagged": _first_sentence(
                verdict.audit.liability_digest.decision_reasoning,
                f"Legacy verdict routed as {verdict.routing.risk_level} risk.",
            )[:220],
            "why_not_benign": (
                "Legacy verdict path escalated this event beyond benign handling."
                if verdict.routing.action == "alert"
                else "Legacy verdict path treated this as non-threatening activity."
            )[:220],
            "what_is_uncertain": (
                "Legacy compatibility path; no structured ambiguity evidence was attached."
                if ambiguity_state in {"ambiguous", "contested"}
                else "No major ambiguity surfaced in legacy compatibility mode."
            )[:220],
            "timeline_context": _first_sentence(
                str(metadata.get("timeline_context", "")),
                "Legacy verdict path; no linked case history was attached.",
            )[:220],
            "recommended_next_step": recommended_next_action[:220],
        }

    evidence_digest = [item.model_dump() for item in verdict.evidence_digest]
    if not evidence_digest:
        evidence_digest = [
            {
                "kind": "vision",
                "claim": (verdict.description or verdict.summary.headline or "Legacy verdict without scene description.")[:180],
                "confidence": float(verdict.audit.liability_digest.confidence_score or 0.0),
                "source": "legacy_verdict",
                "status": "supporting",
            },
            {
                "kind": "routing",
                "claim": f"action={verdict.routing.action}, risk_level={verdict.routing.risk_level}, visibility={verdict.routing.visibility_policy}",
                "confidence": float(verdict.audit.liability_digest.confidence_score or 0.0),
                "source": "routing",
                "status": "supporting" if verdict.routing.action == "alert" else "counter",
            },
            {
                "kind": "policy",
                "claim": _first_sentence(
                    verdict.audit.liability_digest.decision_reasoning,
                    "Legacy verdict path did not provide structured policy reasoning.",
                )[:180],
                "confidence": 1.0,
                "source": "arbiter",
                "status": "supporting",
            },
        ]

    case_payload = verdict.case.model_dump(mode="json") if verdict.case else {}
    case_payload.update(
        {
            "case_id": case_id,
            "case_status": case_status,
            "ambiguity_state": ambiguity_state,
            "confidence_band": confidence_band,
            "consumer_summary": consumer_summary,
            "operator_summary": operator_summary,
            "evidence_digest": evidence_digest,
            "recommended_next_action": recommended_next_action,
            "recommended_delivery_targets": recommended_delivery_targets,
            "threat_patterns": case_payload.get("threat_patterns", []),
            "benign_patterns": case_payload.get("benign_patterns", []),
            "ambiguity_patterns": case_payload.get("ambiguity_patterns", []),
            "perception": verdict.perception.model_dump(mode="json"),
            "judgement": verdict.judgement.model_dump(mode="json"),
            "routing_decision": verdict.routing_decision.model_dump(mode="json"),
            "action_readiness": verdict.action_readiness.model_dump(mode="json"),
        }
    )
    return {
        "case": case_payload,
        "case_id": case_id,
        "case_status": case_status,
        "ambiguity_state": ambiguity_state,
        "confidence_band": confidence_band,
        "consumer_summary": consumer_summary,
        "operator_summary": operator_summary,
        "evidence_digest": evidence_digest,
        "recommended_next_action": recommended_next_action,
        "recommended_delivery_targets": recommended_delivery_targets,
        "perception": verdict.perception.model_dump(mode="json"),
        "judgement": verdict.judgement.model_dump(mode="json"),
        "routing_decision": verdict.routing_decision.model_dump(mode="json"),
        "action_readiness": verdict.action_readiness.model_dump(mode="json"),
    }


def public_verdict(verdict: Verdict) -> dict[str, Any]:
    event_context = verdict.event_context.model_dump(mode="json") if verdict.event_context else None
    case_fields = public_case_fields(verdict)
    payload = {
        "frame_id": verdict.frame_id,
        "event_id": verdict.event_id,
        "stream_id": verdict.stream_id,
        "site_id": verdict.site_id,
        "timestamp": verdict.timestamp.isoformat(),
        "risk_level": verdict.routing.risk_level,
        "action": verdict.routing.action,
        "severity": verdict.routing.severity,
        "visibility_policy": verdict.routing.visibility_policy,
        "notification_policy": verdict.routing.notification_policy,
        "storage_policy": verdict.routing.storage_policy,
        "categories": verdict.routing.categories,
        "summary": verdict.summary.headline,
        "narrative_summary": verdict.summary.narrative,
        "description": verdict.description,
        "bbox": [b.model_dump() for b in verdict.bbox],
        "b64_thumbnail": verdict.b64_thumbnail,
        "agent_outputs": [public_agent_output(output) for output in verdict.audit.agent_outputs],
        "decision_reason": strip_capability_claims(verdict.audit.liability_digest.decision_reasoning or ""),
        "alert_reason": strip_capability_claims(verdict.audit.liability_digest.decision_reasoning or "") if verdict.routing.notification_policy == "immediate" else None,
        "suppress_reason": strip_capability_claims(verdict.audit.liability_digest.decision_reasoning or "") if verdict.routing.visibility_policy == "hidden" else None,
        "reasoning_degraded": is_reasoning_degraded(verdict),
        "event_context": event_context,
        "policy_version": verdict.telemetry.get("policy_version"),
        "prompt_version": verdict.telemetry.get("prompt_version"),
        "autonomy_eligible": verdict.action_readiness.autonomy_eligible,
        "allowed_action_types": list(verdict.action_readiness.allowed_action_types),
        "action_bus": build_action_bus_payload(verdict),
        **case_fields,
    }
    if benchmark_enabled() and verdict.telemetry:
        payload["benchmark_telemetry"] = verdict.telemetry
    return payload
