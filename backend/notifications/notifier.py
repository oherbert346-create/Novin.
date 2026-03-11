from __future__ import annotations

import logging
import os

import httpx

from backend.actions import external_notification_intents
from backend.agent.hallucination_guard import strip_capability_claims
from backend.config import settings
from backend.models.schemas import Verdict
from backend.public import public_verdict

logger = logging.getLogger(__name__)


def _webhook_url_for_home(home_id: str) -> str | None:
    """Resolve webhook URL: WEBHOOK_URL_{home_id} overrides WEBHOOK_URL."""
    key = f"WEBHOOK_URL_{home_id}".upper().replace("-", "_")
    return os.environ.get(key) or settings.webhook_url


async def dispatch(verdict: Verdict) -> None:
    tasks = []
    intents = external_notification_intents(verdict)
    if not intents:
        return
    if settings.shadow_mode:
        await _dispatch_shadow(verdict, intents)
        return
    for intent in intents:
        if intent.target_type == "webhook":
            webhook_url = _webhook_url_for_home(verdict.site_id)
            if webhook_url:
                tasks.append(_send_webhook(verdict, webhook_url))
        elif intent.target_type == "homeowner_app" and settings.slack_webhook_url:
            tasks.append(_send_slack(verdict))
        elif intent.target_type == "operator_queue" and settings.smtp_host and settings.alert_email_to:
            tasks.append(_send_email(verdict))

    import asyncio
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error("Notification error: %s", r)


async def _dispatch_shadow(verdict: Verdict, intents) -> None:
    targets = sorted({intent.target_type for intent in intents})
    logger.info(
        "Shadow mode active: suppressing external notifications for event=%s targets=%s",
        verdict.event_id,
        ",".join(targets),
    )
    if not settings.shadow_webhook_url:
        return

    payload = public_verdict(verdict)
    payload["shadow_mode"] = True
    payload["suppressed_external_targets"] = targets
    payload["home_id"] = verdict.site_id
    payload["event_id"] = verdict.event_id
    payload["cam_id"] = verdict.stream_id
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(settings.shadow_webhook_url, json=payload)
        resp.raise_for_status()
    logger.info(
        "Shadow webhook delivered: event=%s cam=%s → %s",
        verdict.event_id,
        verdict.stream_id,
        settings.shadow_webhook_url,
    )


async def _send_webhook(verdict: Verdict, url: str) -> None:
    payload = public_verdict(verdict)
    payload["home_id"] = verdict.site_id
    payload["event_id"] = verdict.event_id
    payload["cam_id"] = verdict.stream_id
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
    logger.info("Webhook delivered: event=%s cam=%s → %s", verdict.event_id, verdict.stream_id, url)


async def _send_slack(verdict: Verdict) -> None:
    severity_emoji = {
        "none": "✅",
        "low": "🟡",
        "medium": "🟠",
        "high": "🔴",
        "critical": "🚨",
    }
    emoji = severity_emoji.get(verdict.routing.risk_level, "⚠️")
    condensed_narrative = ""
    if verdict.summary.narrative:
        first_sentence = verdict.summary.narrative.strip().split(". ", 1)[0].strip()
        if first_sentence:
            if not first_sentence.endswith("."):
                first_sentence += "."
            condensed_narrative = f"\n*Operator summary:* {first_sentence}"
    
    text = (
        f"{emoji} *HOME SECURITY ALERT* | cam:{verdict.stream_id} | event:{verdict.frame_id}\n"
        f"*Risk level:* {verdict.routing.risk_level.upper()}\n"
        f"*Categories:* {', '.join(verdict.routing.categories)}\n"
        f"*Description:* {verdict.description}\n"
        f"*Time:* {verdict.timestamp.isoformat()}"
        f"{condensed_narrative}"
    )
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(settings.slack_webhook_url, json={"text": text})
        resp.raise_for_status()
    logger.info("Slack notification sent for frame %s", verdict.frame_id)


async def _send_email(verdict: Verdict) -> None:
    try:
        import aiosmtplib
        from email.mime.text import MIMEText

        # Include narrative summary if available
        narrative_section = f"\n\nSECURITY NARRATIVE:\n{verdict.summary.narrative}\n" if verdict.summary.narrative else ""
        
        decision_reasoning = strip_capability_claims(
            verdict.audit.liability_digest.decision_reasoning or ""
        )
        body = (
            f"SECURITY ALERT\n\n"
            f"Stream: {verdict.stream_id}\n"
            f"Risk level: {verdict.routing.risk_level}\n"
            f"Categories: {', '.join(verdict.routing.categories)}\n"
            f"Description: {verdict.description}\n"
            f"Time: {verdict.timestamp.isoformat()}\n\n"
            f"Alert Reason: {decision_reasoning}\n\n"
            f"Agent Reasoning:\n"
            + "\n".join(
                f"  [{o.role}]: {o.verdict} — {strip_capability_claims(o.rationale)[:100]}"
                for o in verdict.audit.agent_outputs
            )
            + narrative_section
        )
        msg = MIMEText(body)
        msg["Subject"] = f"[NOVIN HOME] {verdict.routing.risk_level.upper()} Risk — {verdict.stream_id}"
        msg["From"] = settings.smtp_user or "novin@security"
        msg["To"] = settings.alert_email_to

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_pass,
            start_tls=True,
        )
        logger.info("Email sent for frame %s", verdict.frame_id)
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
