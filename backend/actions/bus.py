from __future__ import annotations

from typing import Any

from backend.models.schemas import ActionIntent, Verdict


def build_action_bus_payload(verdict: Verdict) -> dict[str, Any]:
    return {
        "event_id": verdict.event_id,
        "site_id": verdict.site_id,
        "case_id": verdict.case_id or verdict.case.case_id,
        "autonomy_eligible": verdict.action_readiness.autonomy_eligible,
        "allowed_action_types": list(verdict.action_readiness.allowed_action_types),
        "required_confirmations": list(verdict.action_readiness.required_confirmations),
        "tool_targets": list(verdict.action_readiness.tool_targets),
        "action_intents": [intent.model_dump(mode="json") for intent in verdict.action_readiness.action_intents],
    }


def external_notification_intents(verdict: Verdict) -> list[ActionIntent]:
    intents = list(verdict.action_readiness.action_intents)
    if not intents:
        return []
    if verdict.routing.notification_policy != "immediate":
        return [intent for intent in intents if intent.target_type in {"timeline", "operator_queue"}]
    return [
        intent
        for intent in intents
        if intent.target_type in {"webhook", "homeowner_app", "operator_queue", "monitoring"}
    ]
