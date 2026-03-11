from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.notifications import notifier
from backend.models.schemas import (
    ActionIntent,
    ActionReadiness,
    AgentOutput,
    AuditTrail,
    JudgementDecision,
    LiabilityDigest,
    MachineRouting,
    OperatorSummary,
    Verdict,
)


def _make_verdict() -> Verdict:
    return Verdict(
        frame_id="frame-1",
        event_id="event-1",
        stream_id="cam-1",
        site_id="home",
        timestamp=datetime.utcnow(),
        routing=MachineRouting(
            is_threat=True,
            action="alert",
            risk_level="high",
            severity="high",
            categories=["person", "intrusion"],
            notification_policy="immediate",
        ),
        summary=OperatorSummary(headline="Alert", narrative="Shadow test"),
        audit=AuditTrail(
            liability_digest=LiabilityDigest(
                decision_reasoning="shadow test reasoning",
                confidence_score=0.9,
            ),
            agent_outputs=[
                AgentOutput(
                    agent_id="executive_triage_commander",
                    role="Executive Triage",
                    verdict="alert",
                    confidence=0.9,
                    rationale="shadow test",
                    chain_notes={},
                )
            ],
        ),
        description="test alert",
        action_readiness=ActionReadiness(
            autonomy_eligible="human_confirmation",
            allowed_action_types=["notify"],
            tool_targets=["shadow"],
            action_intents=[
                ActionIntent(action_type="notify", target_type="homeowner_app", target="shadow-homeowner"),
                ActionIntent(action_type="notify", target_type="operator_queue", target="shadow-ops"),
                ActionIntent(action_type="notify", target_type="webhook", target="shadow-webhook"),
            ],
        ),
        judgement=JudgementDecision(action="alert"),
    )


@pytest.mark.asyncio
async def test_shadow_mode_suppresses_external_delivery_without_shadow_sink():
    verdict = _make_verdict()
    with patch.object(notifier.settings, "shadow_mode", True), patch.object(
        notifier.settings, "shadow_webhook_url", None
    ), patch.object(notifier.settings, "slack_webhook_url", "https://slack.example"), patch.object(
        notifier.settings, "smtp_host", "smtp.example"
    ), patch.object(
        notifier.settings, "alert_email_to", "ops@example.com"
    ), patch.object(
        notifier, "_send_webhook", AsyncMock()
    ) as mock_webhook, patch.object(
        notifier, "_send_slack", AsyncMock()
    ) as mock_slack, patch.object(
        notifier, "_send_email", AsyncMock()
    ) as mock_email:
        await notifier.dispatch(verdict)

    mock_webhook.assert_not_awaited()
    mock_slack.assert_not_awaited()
    mock_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_mode_delivers_to_shadow_sink_only():
    verdict = _make_verdict()
    post_mock = AsyncMock()
    response = AsyncMock()
    response.raise_for_status.return_value = None
    post_mock.return_value = response

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        post = post_mock

    with patch.object(notifier.settings, "shadow_mode", True), patch.object(
        notifier.settings, "shadow_webhook_url", "https://shadow.example/hook"
    ), patch("backend.notifications.notifier.httpx.AsyncClient", _Client), patch.object(
        notifier, "_send_webhook", AsyncMock()
    ) as mock_webhook, patch.object(
        notifier, "_send_slack", AsyncMock()
    ) as mock_slack, patch.object(
        notifier, "_send_email", AsyncMock()
    ) as mock_email:
        await notifier.dispatch(verdict)

    mock_webhook.assert_not_awaited()
    mock_slack.assert_not_awaited()
    mock_email.assert_not_awaited()
    post_mock.assert_awaited_once()
