from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from time import perf_counter
from abc import ABC, abstractmethod
from typing import Any

from groq import AsyncGroq
from openai import AsyncOpenAI
from openai import RateLimitError as OpenAIRateLimitError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from backend.config import settings
from backend.models.schemas import AgentOutput, FramePacket
from backend.policy import HOME_SECURITY_RISK_HINTS, IDENTITY_METADATA_KEYS, PROHIBITED_IDENTITY_TERMS
from backend.provider import active_reasoning_model, get_siliconflow_client, get_together_client

logger = logging.getLogger(__name__)

_reasoning_runtime_status: dict[str, Any] = {
    "last_success_at": None,
    "last_error_at": None,
    "last_error": None,
}

_RATIONALE_SECTION_KEYS = ("SIGNAL", "EVIDENCE", "UNCERTAINTY", "DECISION")
ALLOWED_CHAIN_NOTE_KEYS = {
    "focus",
    "threat_outcome",
    "zone_risk",
    "intent",
    "benign_theory",
    "triage",
    "recommended_action",
    "risk_level",
}
_LOW_EVIDENCE_MARKERS = (
    "insufficient evidence",
    "not enough evidence",
    "cannot determine",
    "no evidence",
    "no visible evidence",
    "evidence unavailable",
    "evidence unknown",
    "unclear evidence",
)
_BENIGN_MARKERS = (
    "benign",
    "routine",
    "normal",
    "delivery",
    "routine arrival",
    "home-use pattern",
    "pet",
    "no threat",
    "false positive",
)
_THREAT_MARKERS = (
    "forced entry",
    "intrusion",
    "weapon",
    "break-in",
    "trespass",
    "tamper",
    "hostile",
)
_SOFT_THREAT_MARKERS = (
    "entry approach",
    "entry dwell",
    "suspicious presence",
)
_SILICONFLOW_THINKING_MODELS = (
    "Qwen/Qwen3-8B",
    "Qwen/Qwen3-14B",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-30B-A3B",
    "Qwen/Qwen3-235B-A22B",
    "tencent/Hunyuan-A13B-Instruct",
    "zai-org/GLM-4.6V",
    "zai-org/GLM-4.5V",
    "deepseek-ai/DeepSeek-V3.1",
    "deepseek-ai/DeepSeek-V3.1-Terminus",
    "deepseek-ai/DeepSeek-V3.2-Exp",
    "deepseek-ai/DeepSeek-V3.2",
)
_SHARED_GUARDRAIL_PROMPT = """Shared guardrails:
- You are a residential home-security reasoning agent for Novin Home.
- Policy version: launch-accuracy-v1.
- Prioritize homeowner safety, entry-point risk, tamper, forced entry, after-hours unknown presence, repeated approach patterns, and large wildlife at entry thresholds.
- When strong threat cues such as tamper, forced entry, entry dwell, perimeter progression, or suspicious presence are present at an entry zone, do not downgrade to suppress without direct benign evidence from the provided context.
- Entry approach alone is context-dependent and is not sufficient for alert. Treat it as elevated only when paired with dwell, contact with an entry surface, concealment, repeated probing, property removal, or other explicit threat cues.
- If history or context says similar arrivals or deliveries are common, and no dwell, tamper, removal, or probing is observed, prefer suppress with low risk and timeline retention.
- Large wildlife directly at a doorway, porch threshold, railing, or immediate entry area, especially at night, is a notifiable entry hazard. Prefer alert or review unless direct benign context clearly lowers concern.
- If the provided risk cues include wildlife_near_entry, treat that as direct evidence of an entry hazard even when the visible animal category is generic, uncertain, or labeled as pet.
- Do not downgrade entry-zone tamper, forced-entry cues, or repeated unknown presence to benign without direct evidence from the provided context.
- Optimize for accurate residential security outcomes, not generic scene description or social interpretation.
- Prefer specific home-security terms such as entry approach, entry dwell, tamper, perimeter progression, delivery pattern, routine arrival, routine home-use pattern, and wildlife near entry.
- Never infer identity, resident status, guest status, family membership, or familiarity from appearance alone. A person is only known/trusted if explicit upstream metadata says so.
- Output a user-facing risk_level of none, low, medium, or high.
- Include a recommended_action that tells the home-security system what to do next, such as ignore, keep in timeline, continue monitoring, review promptly, or notify immediately. Prefer keep in timeline or continue monitoring for low-risk routine activity.
- Reason internally in three silent steps: 1) identify concrete security cues, 2) weigh benign explanations against threat cues, 3) choose the lowest risk_level consistent with homeowner safety. Do not expose these steps.
- Keep any hidden model thinking separate from the final answer. The final answer must contain only the required JSON fields.
- Treat the provided observation fields as the full evidence set. Do not assume access to the raw image beyond those grounded summaries.
- Separate observed facts from conclusions. Only cite visible actions, objects, locations, quality limits, history, and peer outputs that were explicitly provided.
- Rationale format is mandatory and must be exactly these four labels:
  SIGNAL: <short risk signal>
  EVIDENCE: <concrete observed evidence from vision/history/peers>
  UNCERTAINTY: <what is missing or uncertain>
  DECISION: <why verdict matches evidence>
- Never fabricate facts not present in provided context.
- Do not claim or imply you have tools, APIs, or capabilities beyond producing this JSON. You cannot take actions; you only output a verdict.
- Start the response with "{" and end it with "}". Do not emit <think> tags, XML tags, comments, analysis text, or any text before or after the JSON object.
- Use strict JSON only: double-quoted keys, double-quoted string values, no trailing commas, no single-quoted keys, and no schema examples.
- Use verdict="uncertain" only when evidence is genuinely ambiguous. If strong threat cues exist at an entry zone, prefer alert over uncertain.
- Keep rationale concise and homeowner-friendly while preserving the exact label format.
- Keep each section short. Do not exceed 55 words total across all four sections.
- Return a single raw JSON object only. No markdown fences, no prefatory text.
- Stay strictly within your assigned cognitive lane. Do not analyze domains assigned to other specialists.
"""
_REASONING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["alert", "suppress", "uncertain"],
        },
        "risk_level": {
            "type": "string",
            "enum": ["none", "low", "medium", "high"],
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "rationale": {
            "type": "string",
        },
        "recommended_action": {
            "type": "string",
        },
        "consumer_headline": {
            "type": "string",
        },
        "consumer_reason": {
            "type": "string",
        },
        "operator_observed": {
            "type": "string",
        },
        "operator_triage": {
            "type": "string",
        },
        "chain_notes": {
            "type": "object",
            "additionalProperties": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "boolean"},
                ]
            },
        },
    },
    "required": ["verdict", "risk_level", "confidence", "rationale", "recommended_action", "chain_notes"],
}
_CEREBRAS_REASONING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["alert", "suppress", "uncertain"],
        },
        "risk_level": {
            "type": "string",
            "enum": ["none", "low", "medium", "high"],
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "rationale": {
            "type": "string",
        },
        "recommended_action": {
            "type": "string",
        },
        "consumer_headline": {
            "type": "string",
        },
        "consumer_reason": {
            "type": "string",
        },
        "operator_observed": {
            "type": "string",
        },
        "operator_triage": {
            "type": "string",
        },
    },
    "required": ["verdict", "risk_level", "confidence", "rationale", "recommended_action"],
}
_cerebras_client: AsyncOpenAI | None = None


class ReasoningAgent(ABC):
    agent_id: str
    role: str
    system_prompt: str
    allowed_verdicts: tuple[str, ...] = ("alert", "suppress", "uncertain")
    chain_defaults: dict[str, Any] = {}

    async def reason(
        self,
        packet: FramePacket,
        client: AsyncGroq,
        peer_outputs: dict[str, Any] | None = None,
    ) -> AgentOutput:
        output, _ = await self.reason_with_metrics(packet, client, peer_outputs=peer_outputs)
        return output

    async def reason_with_metrics(
        self,
        packet: FramePacket,
        client: AsyncGroq,
        peer_outputs: dict[str, Any] | None = None,
    ) -> tuple[AgentOutput, dict[str, Any]]:
        user_content = self._build_user_content(packet, peer_outputs or {})
        return await self._complete_with_retry(client, user_content)

    async def reason_draft(
        self,
        packet: FramePacket,
        client: AsyncGroq,
    ) -> AgentOutput:
        return await self.reason(packet, client, peer_outputs={})

    async def reason_finalize(
        self,
        packet: FramePacket,
        peer_outputs: dict[str, Any],
        client: AsyncGroq,
    ) -> AgentOutput:
        return await self.reason(packet, client, peer_outputs=peer_outputs)

    def _extract_usage(self, response: Any) -> dict[str, int]:
        usage = {}
        if getattr(response, "usage", None) is not None:
            u = response.usage
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or getattr(u, "input_tokens", 0),
                "completion_tokens": getattr(u, "completion_tokens", 0) or getattr(u, "output_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0) or 0,
            }
        return usage

    async def _complete_with_retry(self, client: AsyncGroq, user_content: str) -> tuple[AgentOutput, dict[str, Any]]:
        metrics = {
            "repair_count": 0,
            "local_repair_count": 0,
            "invalid_output_count": 0,
            "latency_ms": 0.0,
            "model_calls": 0,
            "usage": {},
            "fallback": False,
        }
        data, raw, err, latency_ms, usage, invoke_meta = await self._invoke_model(client, user_content)
        metrics["latency_ms"] += latency_ms
        metrics["model_calls"] += 1
        metrics["usage"] = usage
        metrics["local_repair_count"] += int(invoke_meta.get("local_repair_applied", False))
        output = self._validate_output(data, err=err, raw=raw)
        if output is not None:
            return output, metrics
        metrics["invalid_output_count"] += 1

        repair_user = (
            "Repair the previous response into valid JSON matching this schema exactly: "
            "{verdict:'alert|suppress|uncertain', risk_level:'none|low|medium|high', confidence:0..1, rationale:string, recommended_action:string}"
        )
        if settings.reasoning_provider != "cerebras":
            repair_user += ", chain_notes:object}. Use very short chain_notes values."
        else:
            repair_user += "}."
        retry_data, retry_raw, retry_err, retry_latency_ms, retry_usage, retry_meta = await self._invoke_model(
            client,
            repair_user,
            prior_response=raw,
        )
        metrics["repair_count"] = 1
        metrics["latency_ms"] += retry_latency_ms
        metrics["model_calls"] += 1
        metrics["local_repair_count"] += int(retry_meta.get("local_repair_applied", False))
        for k, v in (retry_usage or {}).items():
            metrics["usage"][k] = metrics["usage"].get(k, 0) + v
        retry_output = self._validate_output(retry_data, err=retry_err, raw=retry_raw)
        if retry_output is not None:
            return retry_output, metrics
        metrics["invalid_output_count"] += 1
        metrics["fallback"] = True

        return self._fallback_output(f"invalid_output: {retry_err or err or 'schema_error'}"), metrics

    async def _invoke_model(
        self,
        client: AsyncGroq,
        user_content: str,
        prior_response: str | None = None,
    ) -> tuple[dict[str, Any], str, str | None, float, dict[str, int], dict[str, Any]]:
        raw_result = await self._call_model(client, user_content, prior_response=prior_response)
        if not isinstance(raw_result, tuple):
            return {}, "", "invalid_result", 0.0, {}, {"local_repair_applied": False}
        if len(raw_result) >= 6:
            data, raw, err, latency_ms, usage, meta = raw_result[:6]
            return data, raw, err, float(latency_ms), usage or {}, meta or {"local_repair_applied": False}
        if len(raw_result) == 5:
            data, raw, err, latency_ms, usage = raw_result
            return data, raw, err, float(latency_ms), usage or {}, {"local_repair_applied": False}
        if len(raw_result) == 4:
            data, raw, err, latency_ms = raw_result
            return data, raw, err, float(latency_ms), {}, {"local_repair_applied": False}
        if len(raw_result) == 3:
            data, raw, err = raw_result
            return data, raw, err, 0.0, {}, {"local_repair_applied": False}
        return {}, "", "invalid_result", 0.0, {}, {"local_repair_applied": False}

    @retry(
        retry=retry_if_exception_type((OpenAIRateLimitError, ConnectionError, TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _call_model(
        self,
        client: AsyncGroq,
        user_content: str,
        prior_response: str | None = None,
    ) -> tuple[dict, str, str, float, dict, dict]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": f"{self.system_prompt}\n\n{_SHARED_GUARDRAIL_PROMPT}"},
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

        started = perf_counter()
        local_repair_applied = False
        try:
            if settings.reasoning_provider == "cerebras":
                if not settings.cerebras_api_key:
                    raise RuntimeError("CEREBRAS_API_KEY is required when REASONING_PROVIDER=cerebras")
                cerebras_client = _get_cerebras_client()
                # reasoning_format=hidden drops reasoning from response (tokens still generated but not returned)
                # reasoning_effort=low minimizes thinking for faster responses
                for attempt in range(_CEREBRAS_429_MAX_ATTEMPTS):
                    try:
                        response = await cerebras_client.chat.completions.create(
                            model=active_reasoning_model(),
                            messages=messages,
                            max_completion_tokens=settings.cerebras_max_completion_tokens,
                            reasoning_effort="low",
                            temperature=0.0,
                            response_format={
                                "type": "json_schema",
                                "json_schema": {
                                    "name": "novin_home_reasoning_output",
                                    "strict": True,
                                    "schema": _CEREBRAS_REASONING_RESPONSE_SCHEMA,
                                },
                            },
                            extra_body={"reasoning_format": "hidden"},
                        )
                        content = _extract_json_content(response.choices[0].message.content or "")
                        break
                    except OpenAIRateLimitError as exc:
                        if attempt + 1 >= _CEREBRAS_429_MAX_ATTEMPTS:
                            raise
                        delay = min(2.0 * (2**attempt), 8.0)
                        logger.warning(
                            "Cerebras 429 (attempt %d/%d), retrying in %.1fs: %s",
                            attempt + 1,
                            _CEREBRAS_429_MAX_ATTEMPTS,
                            delay,
                            str(exc)[:120],
                        )
                        await asyncio.sleep(delay)
            elif settings.reasoning_provider == "together":
                if not settings.together_api_key:
                    raise RuntimeError("TOGETHER_API_KEY is required when REASONING_PROVIDER=together")
                together_client = get_together_client()
                response = await together_client.chat.completions.create(
                    model=active_reasoning_model(),
                    messages=messages,
                    max_tokens=settings.together_reasoning_max_tokens,
                    temperature=settings.reasoning_temperature,
                    top_p=settings.reasoning_top_p,
                    response_format={"type": "json_object"},
                )
                content = _extract_json_content(response.choices[0].message.content or "")
            elif settings.reasoning_provider == "siliconflow":
                if not settings.siliconflow_api_key:
                    raise RuntimeError("SILICONFLOW_API_KEY is required when REASONING_PROVIDER=siliconflow")
                siliconflow_client = get_siliconflow_client()
                extra_body: dict[str, Any] = {}
                model_name = active_reasoning_model()
                if any(model_name.startswith(prefix) for prefix in _SILICONFLOW_THINKING_MODELS):
                    extra_body = {
                        "enable_thinking": settings.siliconflow_enable_thinking,
                        "thinking_budget": settings.siliconflow_thinking_budget,
                    }
                response = await siliconflow_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    max_tokens=settings.siliconflow_reasoning_max_tokens,
                    temperature=settings.reasoning_temperature,
                    top_p=settings.reasoning_top_p,
                    extra_body=extra_body,
                )
                content = _extract_json_content(response.choices[0].message.content or "")
            else:
                if client is None:
                    raise RuntimeError("Groq reasoning client is not initialised")
                model_id = active_reasoning_model()
                groq_kw: dict[str, Any] = {
                    "model": model_id,
                    "messages": messages,
                    "max_tokens": settings.groq_reasoning_max_tokens,
                    "temperature": settings.reasoning_temperature,
                    "top_p": settings.reasoning_top_p,
                }
                # gpt-oss: reasoning_format=hidden returns only JSON (required for json_object)
                # Groq SDK doesn't expose reasoning params; pass via extra_body
                if "gpt-oss" in model_id:
                    groq_kw["response_format"] = {"type": "json_object"}
                    groq_kw["extra_body"] = {
                        "reasoning_format": "hidden",
                        "reasoning_effort": "low",
                    }
                # Qwen3 on Groq: reasoning_effort=none disables thinking entirely —
                # no <think> tokens generated or returned, lowest latency + cost.
                # Set GROQ_ENABLE_THINKING=true to allow extended reasoning (slower, higher cost).
                elif "qwen3" in model_id.lower():
                    if settings.groq_enable_thinking:
                        groq_kw["extra_body"] = {"reasoning_effort": "default"}
                    else:
                        groq_kw["extra_body"] = {"reasoning_effort": "none"}
                response = await client.chat.completions.create(**groq_kw)
                content = _extract_json_content(response.choices[0].message.content or "")

            try:
                data = json.loads(content)
            except json.JSONDecodeError as parse_err:
                repaired = _repair_truncated_json(content)
                if repaired:
                    data = json.loads(repaired)
                    content = repaired
                    local_repair_applied = True
                else:
                    raise parse_err
            _mark_reasoning_success()
            usage = self._extract_usage(response)
            return data, content, None, round((perf_counter() - started) * 1000, 2), usage, {
                "local_repair_applied": local_repair_applied,
            }
        except Exception as exc:
            _mark_reasoning_error(str(exc))
            logger.exception("Reasoning agent %s error: %s", self.agent_id, exc)
            return {}, "", str(exc), round((perf_counter() - started) * 1000, 2), {}, {
                "local_repair_applied": local_repair_applied,
            }

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
        risk_level = str(data.get("risk_level", "")).lower().strip()
        if risk_level not in {"none", "low", "medium", "high"}:
            return None

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if confidence > 1.0 and confidence <= 100.0:
            confidence = confidence / 100.0
        confidence = max(0.0, min(1.0, confidence))

        rationale = str(data.get("rationale", "")).strip()
        if not rationale:
            return None
        recommended_action = str(data.get("recommended_action", "")).strip()
        if not recommended_action:
            return None
        consumer_headline = str(data.get("consumer_headline", "")).strip()
        consumer_reason = str(data.get("consumer_reason", "")).strip()
        operator_observed = str(data.get("operator_observed", "")).strip()
        operator_triage = str(data.get("operator_triage", "")).strip()

        chain_notes = data.get("chain_notes", {})
        if not isinstance(chain_notes, dict):
            return None

        merged_notes = dict(self.chain_defaults)
        merged_notes.update(chain_notes)
        dropped_keys = [k for k in merged_notes if k not in ALLOWED_CHAIN_NOTE_KEYS]
        if dropped_keys:
            logger.warning("Dropping chain_notes keys not in allowlist: %s", dropped_keys)
        merged_notes = {k: v for k, v in merged_notes.items() if k in ALLOWED_CHAIN_NOTE_KEYS}
        merged_notes.setdefault("risk_level", risk_level)
        merged_notes.setdefault("recommended_action", recommended_action[:120])
        output = AgentOutput(
            agent_id=self.agent_id,
            role=self.role,
            verdict=verdict,
            risk_level=risk_level,
            confidence=confidence,
            rationale=rationale[:220],
            recommended_action=recommended_action[:140],
            chain_notes=merged_notes,
            consumer_headline=consumer_headline[:80],
            consumer_reason=consumer_reason[:120],
            operator_observed=operator_observed[:220],
            operator_triage=operator_triage[:220],
        )
        guardrail_reason = _guardrail_failure_reason(output)
        if guardrail_reason:
            return self._fallback_output(guardrail_reason)
        return output

    def _fallback_output(self, reason: str) -> AgentOutput:
        return AgentOutput(
            agent_id=self.agent_id,
            role=self.role,
            verdict="uncertain",
            risk_level="low",
            confidence=0.0,
            rationale=f"Agent fallback: {reason}"[:280],
            recommended_action="continue monitoring and review next event",
            chain_notes=dict(self.chain_defaults),
            consumer_headline="",
            consumer_reason="",
            operator_observed="",
            operator_triage="",
        )

    @abstractmethod
    def _build_user_content(self, packet: FramePacket, peer_outputs: dict) -> str:
        ...


def _format_csv(values: list[str], fallback: str, limit: int = 4) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return ",".join(cleaned[:limit]) if cleaned else fallback


def _vision_summary(packet: FramePacket) -> str:
    v = packet.vision
    categories = ",".join(v.categories[:3])
    desc = v.description.replace("\n", " ")[:140]
    risk_labels = [r for r in (v.risk_labels or []) if r and str(r).strip()]
    risk_str = ",".join(risk_labels[:5]) if risk_labels else "clear"
    risk_set = {str(r).strip().lower() for r in risk_labels}
    threat_hint = " [threat_cues]" if risk_set & HOME_SECURITY_RISK_HINTS else ""
    return (
        f"V: threat={int(v.threat)} sev={v.severity} conf={v.confidence:.2f} "
        f"cats={categories or 'clear'} risk={risk_str}{threat_hint} desc='{desc}'"
    )


def _vision_observation_summary(packet: FramePacket) -> str:
    v = packet.vision
    entities = _format_csv(v.observed_entities or v.identity_labels, "clear")
    actions = _format_csv(v.observed_actions, "unclear_action")
    spatial = _format_csv(v.spatial_tags, "unknown_location")
    objects = _format_csv(v.object_labels, "none")
    setting = v.setting or "unknown"
    notes = " | ".join(v.evidence_notes[:2]) if v.evidence_notes else "none"
    return (
        f"OBS: scene={v.scene_status} setting={setting} entities={entities} "
        f"actions={actions} spatial={spatial} objects={objects} notes={notes}"
    )


def _vision_quality_summary(packet: FramePacket) -> str:
    v = packet.vision
    visibility = _format_csv(v.visibility_tags, "clear_view")
    return f"QUAL: visibility={visibility} confidence={v.confidence:.2f} uncertainty={v.uncertainty:.2f}"


def _vision_entry_risk_summary(packet: FramePacket) -> str:
    v = packet.vision
    risk = _format_csv(v.risk_labels, "clear", limit=5)
    categories = _format_csv(v.categories, "clear", limit=4)
    return f"RISK: threat={int(v.threat)} severity={v.severity} risk_labels={risk} categories={categories}"


def _vision_agent_view(packet: FramePacket, agent_id: str) -> str:
    shared = [
        _vision_observation_summary(packet),
        _vision_quality_summary(packet),
        _vision_entry_risk_summary(packet),
    ]
    if agent_id == "context_baseline_reasoner":
        shared.append("LANE: use setting, zone relevance, and visibility limits; do not infer intent.")
    elif agent_id == "trajectory_intent_assessor":
        shared.append("LANE: use only observed actions, spatial cues, objects, and uncertainty; do not infer psychology or gaze.")
    elif agent_id == "falsification_auditor":
        shared.append("LANE: test only benign explanations supported by these observations and limits.")
    else:
        shared.append("LANE: resolve conflicts using grounded observations, uncertainty, and peer outputs only.")
    return "\n".join(shared)


def _history_summary(packet: FramePacket) -> str:
    h = packet.history
    top_similar = ",".join(e.severity for e in h.similar_events[:2]) or "none"
    baseline_total = round(sum(float(v) for v in h.camera_baseline.values()), 3)
    recent_ts = ""
    if h.recent_events:
        now = packet.timestamp
        mins = [(now - e.timestamp).total_seconds() / 60 for e in h.recent_events[:2]]
        if mins:
            recent_ts = f" last_alert={mins[0]:.0f}m ago"
    return (
        f"H: recent={len(h.recent_events)} similar={len(h.similar_events)}{recent_ts} "
        f"anomaly={h.anomaly_score:.2f} baseline={baseline_total:.2f} top_similar={top_similar}"
    )


def _stream_summary(packet: FramePacket) -> str:
    m = packet.stream_meta
    metadata = packet.event_context.metadata if packet.event_context else {}
    trust_markers: list[str] = []
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if str(key).strip().lower() in IDENTITY_METADATA_KEYS and bool(value):
                trust_markers.append(f"{str(key).strip().lower()}=true")
    trust_summary = ",".join(trust_markers) if trust_markers else "none"
    return f"CTX: camera='{m.label[:24]}' zone='{m.zone[:18]}' site='{m.site_id[:18]}' trust={trust_summary}"


def _memory_summary(packet: FramePacket) -> str:
    if not _should_include_memory(packet):
        return "MEM: none"
    ranked = sorted(packet.history.memory_items, key=_memory_rank, reverse=True)
    entries = []
    for memory in ranked[:2]:
        details = memory.details or {}
        preference_tags = ",".join(str(tag) for tag in details.get("preference_tags", [])[:2]) or "-"
        event_type = str(details.get("event_type", memory.memory_key.split(":", 1)[0]))[:18]
        last_action = str(details.get("last_action", "-"))[:10]
        last_seen_minutes = details.get("last_seen_minutes")
        last_seen_text = f"{int(last_seen_minutes)}m" if isinstance(last_seen_minutes, int) else "na"
        scope = "thread" if memory.scope_type == "source_event" else memory.scope_type
        text = (
            f"{scope}|type={event_type}|last={last_action}|seen={last_seen_text}|"
            f"hits={memory.hit_count}|prefs={preference_tags}"
        )
        entries.append(text[:96])
    if not entries:
        return "MEM: none"
    return "MEM: " + " | ".join(entries)


def _preference_summary(packet: FramePacket) -> str:
    metadata = packet.event_context.metadata if packet.event_context else {}
    if not isinstance(metadata, dict):
        return "PREF: none"
    preferences = metadata.get("preferences", {})
    if not isinstance(preferences, dict) or not preferences:
        return "PREF: none"
    items = [f"{str(key)[:16]}={str(value)[:18]}" for key, value in list(preferences.items())[:2]]
    return "PREF: " + " | ".join(items)


def _mark_reasoning_success() -> None:
    _reasoning_runtime_status["last_success_at"] = datetime.now(timezone.utc)
    _reasoning_runtime_status["last_error"] = None


_CEREBRAS_429_MAX_ATTEMPTS = 5  # initial + 4 retries for queue_exceeded
_CEREBRAS_429_MAX_RETRIES = 4  # OpenAI client built-in retries


def _get_cerebras_client() -> AsyncOpenAI:
    global _cerebras_client
    if _cerebras_client is None:
        _cerebras_client = AsyncOpenAI(
            api_key=settings.cerebras_api_key,
            base_url=settings.cerebras_base_url,
            timeout=None,
            max_retries=_CEREBRAS_429_MAX_RETRIES,
        )
    return _cerebras_client


async def warmup_cerebras_reasoning() -> None:
    """Send a minimal Cerebras completion at startup to keep the model warm and avoid cold-queue 429s."""
    if settings.reasoning_provider != "cerebras" or not settings.cerebras_api_key:
        return
    try:
        client = _get_cerebras_client()
        await client.chat.completions.create(
            model=active_reasoning_model(),
            messages=[{"role": "user", "content": "warmup"}],
            max_completion_tokens=5,
            temperature=0.0,
        )
        logger.info("Cerebras reasoning warmup completed")
    except Exception as exc:
        logger.warning("Cerebras warmup failed (non-fatal): %s", exc)


def _mark_reasoning_error(message: str) -> None:
    _reasoning_runtime_status["last_error_at"] = datetime.now(timezone.utc)
    _reasoning_runtime_status["last_error"] = message[:280]


def get_reasoning_runtime_status() -> dict[str, Any]:
    last_success = _reasoning_runtime_status["last_success_at"]
    last_error = _reasoning_runtime_status["last_error_at"]
    live = bool(last_success and (not last_error or last_success >= last_error))
    return {
        "live": live,
        "last_success_at": last_success.isoformat() if last_success else None,
        "last_error_at": last_error.isoformat() if last_error else None,
        "last_error": _reasoning_runtime_status["last_error"],
    }


def _peer_summary(peer_outputs: dict) -> str:
    if not peer_outputs:
        return "P: none"
    parts = []
    for _, output in list(peer_outputs.items())[:2]:
        if isinstance(output, AgentOutput):
            parts.append(f"{output.agent_id}:{output.verdict}@{output.confidence:.2f}")
    return "P: " + " | ".join(parts) if parts else "P: none"


def _cognitive_chain_summary(peer_outputs: dict) -> str:
    """Rich summary of prior agents' reasoning for sequential cognitive pipeline."""
    if not peer_outputs:
        return ""
    lines: list[str] = []
    for agent_id, output in peer_outputs.items():
        if isinstance(output, AgentOutput):
            lines.append(
                f"[{agent_id}] verdict={output.verdict} risk_level={output.risk_level} conf={output.confidence:.2f}"
            )
            lines.append(f"Rationale: {output.rationale[:200]}")
            lines.append(f"Action: {output.recommended_action}")
    return "\n".join(lines) if lines else ""


def _should_include_memory(packet: FramePacket) -> bool:
    if not packet.history.memory_items:
        return False
    metadata = packet.event_context.metadata if packet.event_context else {}
    preferences = metadata.get("preferences", {}) if isinstance(metadata, dict) else {}
    has_thread_memory = any(m.scope_type == "source_event" for m in packet.history.memory_items)
    has_recent_activity = bool(packet.history.recent_events) or packet.history.anomaly_score >= 0.5
    threat_like = packet.vision.threat or packet.vision.severity in {"medium", "high", "critical"}
    return has_thread_memory or has_recent_activity or bool(preferences) or threat_like


def _memory_rank(memory) -> tuple[int, int, int]:
    details = memory.details or {}
    return (
        1 if memory.scope_type == "source_event" else 0,
        1 if details.get("preference_tags") else 0,
        int(memory.hit_count),
    )


def _normalize_repaired_json_obj(obj: dict[str, Any]) -> str | None:
    if not isinstance(obj, dict):
        return None
    if not obj.get("verdict") or not obj.get("risk_level") or obj.get("confidence") is None:
        return None
    if not str(obj.get("rationale", "")).strip():
        obj["rationale"] = (
            "SIGNAL: partial response recovered. "
            "EVIDENCE: valid JSON fields were preserved. "
            "UNCERTAINTY: details were truncated. "
            "DECISION: retain the recovered verdict."
        )
    if not str(obj.get("recommended_action", "")).strip():
        obj["recommended_action"] = "continue monitoring"
    if "chain_notes" not in obj or not isinstance(obj.get("chain_notes"), dict):
        obj["chain_notes"] = {}
    return json.dumps(obj, separators=(",", ":"))


def _close_open_json_content(content: str) -> str:
    cleaned = _extract_json_content(content)
    out: list[str] = []
    closers: list[str] = []
    in_string = False
    escape = False
    for ch in cleaned:
        out.append(ch)
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            closers.append("}")
        elif ch == "[":
            closers.append("]")
        elif ch in {"}", "]"} and closers and closers[-1] == ch:
            closers.pop()
    if in_string:
        out.append('"')
    while closers:
        out.append(closers.pop())
    return "".join(out)


def _truncate_to_last_top_level_pair(content: str) -> str | None:
    cleaned = _extract_json_content(content)
    depth = 0
    in_string = False
    escape = False
    last_comma = -1
    for idx, ch in enumerate(cleaned):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 1:
            last_comma = idx
    if last_comma == -1:
        return None
    return cleaned[:last_comma] + "}"


def _repair_truncated_json(content: str) -> str | None:
    """Attempt to repair truncated JSON before falling back to API retry."""
    if not content or "{" not in content:
        return None

    candidates = [
        _extract_json_content(content),
        _close_open_json_content(content),
    ]
    truncated = _truncate_to_last_top_level_pair(content)
    if truncated:
        candidates.append(truncated)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        repaired = _normalize_repaired_json_obj(obj)
        if repaired:
            return repaired
    return None


def _extract_json_content(content: str) -> str:
    cleaned = content.strip()
    
    # Strip <think>...</think> tags from Qwen3 and similar thinking models
    # These models output reasoning in <think> blocks before the JSON
    think_end = cleaned.rfind("</think>")
    if think_end != -1:
        cleaned = cleaned[think_end + 8:].strip()
    elif cleaned.startswith("<think>"):
        # Incomplete think block - find first { after any think content
        first_brace = cleaned.find("{")
        if first_brace != -1:
            cleaned = cleaned[first_brace:]
    
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    candidates: list[str] = []
    for start in [idx for idx, ch in enumerate(cleaned) if ch == "{"]:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(cleaned)):
            ch = cleaned[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(cleaned[start : idx + 1])
                    break

    fallback_candidate = None
    for candidate in reversed(candidates):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and any(key in obj for key in ("verdict", "risk_level", "recommended_action", "rationale")):
            return candidate
        if fallback_candidate is None:
            fallback_candidate = candidate
    if fallback_candidate is not None:
        return fallback_candidate
    if candidates:
        return candidates[-1]

    start = cleaned.rfind("{")
    if start != -1:
        cleaned = cleaned[start:]
    return cleaned


def _extract_rationale_sections(rationale: str) -> dict[str, str]:
    sections = {
        k.upper(): v.strip()
        for k, v in re.findall(
            r"(SIGNAL|EVIDENCE|UNCERTAINTY|DECISION):\s*(.+?)(?=\s*(?:SIGNAL|EVIDENCE|UNCERTAINTY|DECISION):|$)",
            rationale,
            flags=re.IGNORECASE | re.DOTALL,
        )
    }
    return sections


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


_NEGATION_PREFIXES = re.compile(
    r"(?:no|not|without|non|none|cannot|can't|couldn't|absent|lacks?|missing)\s+",
    re.IGNORECASE,
)


def _contains_affirmed_benign(text: str) -> bool:
    """Return True only when a benign marker appears and is NOT immediately
    preceded by a negation word.  Prevents 'no delivery context' or
    'not a routine arrival' from being counted as a benign signal."""
    lowered = text.lower()
    for marker in _BENIGN_MARKERS:
        idx = lowered.find(marker)
        while idx != -1:
            prefix = lowered[max(0, idx - 12) : idx]
            if not _NEGATION_PREFIXES.search(prefix + " "):
                return True
            idx = lowered.find(marker, idx + 1)
    return False


def _contains_identity_term(text: str) -> bool:
    """Check PROHIBITED_IDENTITY_TERMS with word-boundary matching to avoid
    false positives like 'known person' matching inside 'unknown person'."""
    lowered = text.lower()
    for term in PROHIBITED_IDENTITY_TERMS:
        if " " in term:
            if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", lowered):
                return True
        else:
            if re.search(r"\b" + re.escape(term) + r"\b", lowered):
                return True
    return False


def _guardrail_failure_reason(output: AgentOutput) -> str | None:
    sections = _extract_rationale_sections(output.rationale)
    if any(not sections.get(key) for key in _RATIONALE_SECTION_KEYS):
        return "prompt_drift: rationale_format"

    evidence_text = sections["EVIDENCE"]
    uncertainty_text = sections["UNCERTAINTY"]
    decision_text = sections["DECISION"]
    signal_text = sections["SIGNAL"]

    evidence_is_low = len(evidence_text) < 16 or _contains_any(evidence_text, _LOW_EVIDENCE_MARKERS)
    if output.confidence >= 0.65 and evidence_is_low:
        return "low_evidence: high_confidence_claim"

    decision_bundle = f"{signal_text} {evidence_text} {decision_text}"
    has_benign = _contains_affirmed_benign(decision_bundle)
    has_hard_threat = _contains_any(decision_bundle, _THREAT_MARKERS)
    has_any_threat = has_hard_threat or _contains_any(decision_bundle, _SOFT_THREAT_MARKERS)
    if output.verdict == "alert" and has_benign and not has_any_threat:
        return "contradiction: alert_without_threat_evidence"
    if output.verdict == "suppress" and has_hard_threat and not has_benign:
        return "contradiction: suppress_with_threat_evidence"
    all_text = f"{output.rationale} {output.recommended_action}"
    if _contains_identity_term(all_text):
        return "privacy_policy: inferred_identity"
    return None
