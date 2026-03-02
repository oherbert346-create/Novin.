from __future__ import annotations

import json
import logging

import httpx

from backend.config import settings
from backend.models.schemas import Verdict

logger = logging.getLogger(__name__)


async def dispatch(verdict: Verdict) -> None:
    if verdict.routing.action != "alert":
        return
    tasks = []
    if settings.webhook_url:
        tasks.append(_send_webhook(verdict))
    if settings.slack_webhook_url:
        tasks.append(_send_slack(verdict))
    if settings.smtp_host and settings.alert_email_to:
        tasks.append(_send_email(verdict))

    import asyncio
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error("Notification error: %s", r)


async def _send_webhook(verdict: Verdict) -> None:
    payload = verdict.model_dump(mode="json")
    payload.pop("b64_thumbnail", None)
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(settings.webhook_url, json=payload)
        resp.raise_for_status()
    logger.info("Webhook delivered: %s → %s", verdict.frame_id, settings.webhook_url)


async def _send_slack(verdict: Verdict) -> None:
    severity_emoji = {
        "none": "✅",
        "low": "🟡",
        "medium": "🟠",
        "high": "🔴",
        "critical": "🚨",
    }
    emoji = severity_emoji.get(verdict.routing.severity, "⚠️")
    condensed_narrative = ""
    if verdict.summary.narrative:
        first_sentence = verdict.summary.narrative.strip().split(". ", 1)[0].strip()
        if first_sentence:
            if not first_sentence.endswith("."):
                first_sentence += "."
            condensed_narrative = f"\n*Operator summary:* {first_sentence}"
    
    text = (
        f"{emoji} *SECURITY ALERT* | {verdict.stream_id}\n"
        f"*Severity:* {verdict.routing.severity.upper()} | *Confidence:* {verdict.audit.liability_digest.confidence_score:.0%}\n"
        f"*Categories:* {', '.join(verdict.routing.categories)}\n"
        f"*Description:* {verdict.description}\n"
        f"*Time:* {verdict.timestamp.isoformat()}"
        f"{condensed_narrative}"
    )
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(settings.slack_webhook_url, json={"text": text})
        resp.raise_for_status()
    logger.info("Slack notification sent for frame %s", verdict.frame_id)


async def _send_email(verdict: Verdict) -> None:
    try:
        import aiosmtplib
        from email.mime.text import MIMEText

        # Include narrative summary if available
        narrative_section = f"\n\nSECURITY NARRATIVE:\n{verdict.summary.narrative}\n" if verdict.summary.narrative else ""
        
        body = (
            f"SECURITY ALERT\n\n"
            f"Stream: {verdict.stream_id}\n"
            f"Severity: {verdict.routing.severity}\n"
            f"Confidence: {verdict.audit.liability_digest.confidence_score:.0%}\n"
            f"Categories: {', '.join(verdict.routing.categories)}\n"
            f"Description: {verdict.description}\n"
            f"Time: {verdict.timestamp.isoformat()}\n\n"
            f"Alert Reason: {verdict.audit.liability_digest.decision_reasoning}\n\n"
            f"Agent Reasoning:\n"
            + "\n".join(
                f"  [{o.role}]: {o.verdict} ({o.confidence:.0%}) — {o.rationale[:100]}"
                for o in verdict.audit.agent_outputs
            )
            + narrative_section
        )
        msg = MIMEText(body)
        msg["Subject"] = f"[NOVIN] {verdict.severity.upper()} Alert — {verdict.stream_id}"
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
