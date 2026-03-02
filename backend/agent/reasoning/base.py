from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from groq import AsyncGroq

from backend.config import settings
from backend.models.schemas import AgentOutput, FramePacket

logger = logging.getLogger(__name__)


class ReasoningAgent(ABC):
    agent_id: str
    role: str
    system_prompt: str
    allowed_verdicts: tuple[str, ...] = ("alert", "suppress", "uncertain")
    chain_defaults: dict[str, Any] = {}

    async def reason_draft(
        self,
        packet: FramePacket,
        client: AsyncGroq,
    ) -> AgentOutput:
        user_content = self._build_user_content(packet, {})
        return await self._complete_with_retry(client, user_content)

    async def reason_finalize(
        self,
        packet: FramePacket,
        peer_outputs: dict[str, Any],
        client: AsyncGroq,
    ) -> AgentOutput:
        user_content = self._build_user_content(packet, peer_outputs)
        return await self._complete_with_retry(client, user_content)

    async def _complete_with_retry(self, client: AsyncGroq, user_content: str) -> AgentOutput:
        data, raw, err = await self._call_model(client, user_content)
        output = self._validate_output(data, err=err, raw=raw)
        if output is not None:
            return output

        repair_user = (
            "Repair the previous response into valid JSON matching this schema exactly: "
            "{verdict:'alert|suppress|uncertain', confidence:0..1, rationale:string, chain_notes:object}."
        )
        retry_data, retry_raw, retry_err = await self._call_model(
            client,
            repair_user,
            prior_response=raw,
        )
        retry_output = self._validate_output(retry_data, err=retry_err, raw=retry_raw)
        if retry_output is not None:
            return retry_output

        return self._fallback_output(f"invalid_output: {retry_err or err or 'schema_error'}")

    async def _call_model(
        self,
        client: AsyncGroq,
        user_content: str,
        prior_response: str | None = None,
    ) -> tuple[dict[str, Any], str, str | None]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]
        if prior_response:
            messages.extend(
                [
                    {"role": "assistant", "content": prior_response[:700]},
                    {
                        "role": "user",
                        "content": "Return only the corrected JSON object. No prose.",
                    },
                ]
            )

        try:
            if settings.reasoning_provider == "cerebras":
                if not settings.cerebras_api_key:
                    raise RuntimeError("CEREBRAS_API_KEY is required when REASONING_PROVIDER=cerebras")

                url = f"{settings.cerebras_base_url.rstrip('/')}/chat/completions"
                payload = {
                    "model": settings.cerebras_reasoning_model,
                    "messages": messages,
                    "max_tokens": 140,
                    "temperature": 0.2,
                }
                headers = {
                    "Authorization": f"Bearer {settings.cerebras_api_key}",
                    "Content-Type": "application/json",
                }
                async with httpx.AsyncClient(timeout=15.0) as http_client:
                    response = await http_client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    data_json = response.json()
                content = _extract_json_content(
                    data_json.get("choices", [{}])[0].get("message", {}).get("content", "")
                )
            else:
                if client is None:
                    raise RuntimeError("Groq reasoning client is not initialised")
                response = await client.chat.completions.create(
                    model=settings.groq_reasoning_model,
                    messages=messages,
                    max_tokens=140,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
                content = _extract_json_content(response.choices[0].message.content or "")

            data = json.loads(content)
            return data, content, None
        except Exception as exc:
            logger.exception("Reasoning agent %s error: %s", self.agent_id, exc)
            return {}, "", str(exc)

    def _validate_output(
        self,
        data: dict[str, Any],
        *,
        err: str | None,
        raw: str,
    ) -> AgentOutput | None:
        if not isinstance(data, dict):
            return None

        verdict = str(data.get("verdict", "")).lower().strip()
        if verdict not in self.allowed_verdicts:
            return None

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        confidence = max(0.0, min(1.0, confidence))

        rationale = str(data.get("rationale", "")).strip()
        if not rationale:
            return None

        chain_notes = data.get("chain_notes", {})
        if not isinstance(chain_notes, dict):
            return None

        merged_notes = dict(self.chain_defaults)
        merged_notes.update(chain_notes)
        return AgentOutput(
            agent_id=self.agent_id,
            role=self.role,
            verdict=verdict,
            confidence=confidence,
            rationale=rationale[:280],
            chain_notes=merged_notes,
        )

    def _fallback_output(self, reason: str) -> AgentOutput:
        return AgentOutput(
            agent_id=self.agent_id,
            role=self.role,
            verdict="uncertain",
            confidence=0.0,
            rationale=f"Agent fallback: {reason}"[:280],
            chain_notes=dict(self.chain_defaults),
        )

    @abstractmethod
    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        ...


def _vision_summary(packet: FramePacket) -> str:
    v = packet.vision
    categories = ",".join(v.categories[:3])
    desc = v.description.replace("\n", " ")[:90]
    return (
        f"V: threat={int(v.threat)} sev={v.severity} conf={v.confidence:.2f} "
        f"cats={categories or 'clear'} desc='{desc}'"
    )


def _history_summary(packet: FramePacket) -> str:
    h = packet.history
    top_similar = ",".join(e.severity for e in h.similar_events[:2]) or "none"
    baseline_total = round(sum(float(v) for v in h.camera_baseline.values()), 3)
    return (
        f"H: recent={len(h.recent_events)} similar={len(h.similar_events)} "
        f"anomaly={h.anomaly_score:.2f} baseline={baseline_total:.2f} top_similar={top_similar}"
    )


def _stream_summary(packet: FramePacket) -> str:
    m = packet.stream_meta
    return f"CTX: camera='{m.label[:24]}' zone='{m.zone[:18]}' site='{m.site_id[:18]}'"


def _peer_summary(peer_outputs: dict) -> str:
    if not peer_outputs:
        return "P: none"
    parts = []
    for _, output in list(peer_outputs.items())[:3]:
        if isinstance(output, AgentOutput):
            parts.append(
                f"{output.agent_id}:{output.verdict}@{output.confidence:.2f} "
                f"note='{output.rationale[:50]}'"
            )
    return "P: " + " | ".join(parts) if parts else "P: none"


def _extract_json_content(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return cleaned
