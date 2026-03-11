from __future__ import annotations

import argparse
import base64
import json
import pathlib
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


PRIMARY_GATE_SUITE = "staged_real_home"
SECONDARY_SUITES = {"synthetic_home", "public_surveillance", "smoke_pipeline"}
ALL_SUITES = {PRIMARY_GATE_SUITE, *SECONDARY_SUITES}
SOURCE_MODES = {"nvr_webhook", "cloud_alert", "stream_sampled"}


def _default_expected_risk(action: str) -> str:
    return "high" if action == "alert" else "none"


def _default_visibility(risk_level: str) -> str:
    if risk_level == "high":
        return "prominent"
    if risk_level in {"medium", "low"}:
        return "prominent" if risk_level == "medium" else "timeline"
    return "hidden"


def _default_notification(risk_level: str) -> str:
    return "immediate" if risk_level == "high" else ("review" if risk_level == "medium" else "none")


def _default_expected_uncertainty(risk_level: str) -> str:
    return "low"


def _default_autonomy_eligibility(action: str, risk_level: str) -> str:
    if action == "alert" and risk_level == "high":
        return "human_confirmation"
    if action == "suppress" and risk_level in {"none", "low"}:
        return "low_risk_later"
    return "not_eligible"


@dataclass
class EvalCase:
    scenario_id: str
    scenario_family: str
    source_mode: str
    suite: str
    case_id: str
    image_path: str | None
    image_url: str | None
    expected_action: str
    cam_id: str
    home_id: str
    zone: str
    cohort: str
    expected_risk_level: str = "none"
    expected_visibility_policy: str = "hidden"
    expected_notification_policy: str = "none"
    benchmark_kind: str = "action"
    mode: str = "canonical"
    source: str | None = None
    source_event_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    expected_reasoning_short: str = ""
    expected_event_context: dict[str, Any] = field(default_factory=dict)
    expect_memory_items: bool = False
    expect_duplicate_status: bool = False
    expected_status: str | None = None
    repeated_request: bool = False
    direct_label: str = "eval-frame"
    sequence_id: str | None = None
    frame_index: int | None = None
    time_of_day: str = "day"
    indoor_outdoor: str = "outdoor"
    scenario_type: str = "routine"
    preferences_relevant: bool = False
    memory_expected_to_help: bool = False
    quality_grade: str = "reviewed"
    source_type: str = "manual"
    benchmark_eligibility: str = "gating"
    generated_from_case_id: str | None = None
    generator_type: str | None = None
    prompt_version: str | None = None
    augmentation_type: str | None = None
    incident_id_expected: str | None = None
    linked_event_ids_expected: list[str] = field(default_factory=list)
    cross_cam_correlation_expected: bool = False
    escalation_expected: list[str] = field(default_factory=list)
    final_incident_action_expected: str | None = None
    expected_uncertainty_behavior: str = "low"
    expected_autonomy_eligibility: str | None = None


@dataclass
class EvalResult:
    case: EvalCase
    repeat: int
    memory_mode: str
    status_code: int
    action: str
    risk_level: str
    visibility_policy: str
    notification_policy: str
    ok: bool
    latency_s: float
    reasoning_degraded: bool
    fallback_agent_count: int
    context_ok: bool
    idempotency_ok: bool
    memory_present: bool
    case_fields_ok: bool
    consumer_summary_ok: bool
    operator_summary_ok: bool
    evidence_digest_ok: bool
    judgement_contract_ok: bool
    explanation_contract_ok: bool
    routing_contract_ok: bool
    action_readiness_ok: bool
    intelligence_quality_ok: bool
    error: str = ""
    response_status: str = ""
    response_json: dict[str, Any] = field(default_factory=dict)
    benchmark_telemetry: dict[str, Any] = field(default_factory=dict)


def _variant_matrix(raw: dict[str, Any]) -> list[dict[str, str]]:
    axes = raw.get("variant_axes", {})
    if not axes:
        return [{}]
    variants = [{}]
    for axis_name, values in axes.items():
        next_variants: list[dict[str, str]] = []
        for variant in variants:
            for value in values:
                expanded = dict(variant)
                expanded[str(axis_name)] = str(value)
                next_variants.append(expanded)
        variants = next_variants
    return variants


def _variant_slug(variant: dict[str, str]) -> str:
    if not variant:
        return "base"
    return "__".join(f"{key}-{value}" for key, value in sorted(variant.items()))


def _asset_for_event(scenario: dict[str, Any], event: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    asset_key = event.get("asset_ref") or event.get("event_id") or "default"
    asset = assets.get(asset_key) or assets.get("default")
    if not isinstance(asset, dict):
        raise ValueError(f"Scenario {scenario.get('scenario_id')} missing asset mapping for {asset_key}")
    return asset


def _expand_scenario_catalog(raw: dict[str, Any], path: pathlib.Path) -> list[dict[str, Any]]:
    scenarios = raw.get("scenarios", [])
    if not isinstance(scenarios, list):
        raise ValueError(f"Scenario catalog at {path} must define a scenarios list")

    expanded: list[dict[str, Any]] = []
    global_axes = raw.get("variant_axes", {})
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError(f"Scenario catalog at {path} contains a non-object scenario")
        scenario_id = str(scenario.get("scenario_id", "")).strip()
        if not scenario_id:
            raise ValueError(f"Scenario catalog at {path} contains a scenario without scenario_id")
        source_mode = str(scenario.get("source_mode", "nvr_webhook")).strip().lower()
        if source_mode not in SOURCE_MODES:
            raise ValueError(f"Scenario {scenario_id} has unsupported source_mode={source_mode}")
        suite = str(scenario.get("suite", "synthetic_home")).strip().lower()
        if suite not in ALL_SUITES:
            raise ValueError(f"Scenario {scenario_id} has unsupported suite={suite}")
        assets = scenario.get("assets", {})
        timeline = scenario.get("timeline", [])
        cameras = {camera["camera_id"]: camera for camera in scenario.get("cameras", []) if isinstance(camera, dict)}
        correlation = scenario.get("correlation_expectations", {})
        memory = scenario.get("memory_expectations", {})
        scenario_axes = dict(global_axes)
        scenario_axes.update(scenario.get("variant_axes", {}))
        for variant in _variant_matrix({"variant_axes": scenario_axes}):
            variant_slug = _variant_slug(variant)
            generated_from_case_id = scenario.get("generated_from_case_id") or scenario_id
            for event_index, event in enumerate(timeline, start=1):
                camera = cameras.get(event.get("camera_ref"), {})
                asset = _asset_for_event(scenario, event, assets)
                source = event.get("source")
                if not source:
                    source = {
                        "nvr_webhook": "frigate",
                        "cloud_alert": "wyze",
                        "stream_sampled": "stream_sampler",
                    }[source_mode]
                source_event_id = event.get("source_event_id")
                if source_event_id is None:
                    source_event_id = f"{scenario_id}-{variant_slug}-{event.get('event_id', event_index)}"
                mode = "direct_frame" if source_mode == "stream_sampled" else "canonical"
                case_id = f"{scenario_id}__{variant_slug}__{event.get('event_id', event_index)}"
                metadata = dict(scenario.get("metadata", {}))
                metadata.update(dict(event.get("metadata", {})))
                metadata["scenario_id"] = scenario_id
                metadata["scenario_family"] = scenario.get("scenario_family", "synthetic_routine")
                metadata["source_mode"] = source_mode
                metadata["variant"] = variant
                metadata["event_offset_seconds"] = event.get("time_offset_s", 0)
                if source_mode == "stream_sampled":
                    metadata["synthetic_stream_sample"] = True
                    metadata["sample_rate"] = event.get("sample_rate", scenario.get("sample_rate", 30))
                if memory.get("preference_tags"):
                    metadata["preferences"] = {"tags": list(memory.get("preference_tags", []))}
                expected_event_context = {
                    "cam_id": camera.get("camera_id", event.get("camera_ref", f"{scenario_id}-cam")),
                    "home_id": scenario.get("home_id", "synthetic-home"),
                    "zone": event.get("zone", camera.get("zone", "front_door")),
                }
                if source_mode != "stream_sampled":
                    expected_event_context["source"] = source
                expanded.append(
                    {
                        "scenario_id": scenario_id,
                        "scenario_family": scenario.get("scenario_family", "synthetic_routine"),
                        "source_mode": source_mode,
                        "suite": suite,
                        "case_id": case_id,
                        "image_path": asset.get("image_path"),
                        "image_url": asset.get("image_url"),
                        "expected_action": event.get(
                            "expected_action",
                            correlation.get("final_incident_action_expected", "suppress"),
                        ),
                        "expected_risk_level": event.get(
                            "expected_risk_level",
                            _default_expected_risk(
                                event.get("expected_action", correlation.get("final_incident_action_expected", "suppress"))
                            ),
                        ),
                        "expected_visibility_policy": event.get(
                            "expected_visibility_policy",
                            _default_visibility(
                                event.get(
                                    "expected_risk_level",
                                    _default_expected_risk(
                                        event.get("expected_action", correlation.get("final_incident_action_expected", "suppress"))
                                    ),
                                )
                            ),
                        ),
                        "expected_notification_policy": event.get(
                            "expected_notification_policy",
                            _default_notification(
                                event.get(
                                    "expected_risk_level",
                                    _default_expected_risk(
                                        event.get("expected_action", correlation.get("final_incident_action_expected", "suppress"))
                                    ),
                                )
                            ),
                        ),
                        "expected_uncertainty_behavior": event.get(
                            "expected_uncertainty_behavior",
                            _default_expected_uncertainty(
                                event.get(
                                    "expected_risk_level",
                                    _default_expected_risk(
                                        event.get("expected_action", correlation.get("final_incident_action_expected", "suppress"))
                                    ),
                                )
                            ),
                        ),
                        "expected_autonomy_eligibility": event.get(
                            "expected_autonomy_eligibility",
                            _default_autonomy_eligibility(
                                event.get("expected_action", correlation.get("final_incident_action_expected", "suppress")),
                                event.get(
                                    "expected_risk_level",
                                    _default_expected_risk(
                                        event.get("expected_action", correlation.get("final_incident_action_expected", "suppress"))
                                    ),
                                ),
                            ),
                        ),
                        "cam_id": camera.get("camera_id", event.get("camera_ref", f"{scenario_id}-cam")),
                        "home_id": scenario.get("home_id", "synthetic-home"),
                        "zone": event.get("zone", camera.get("zone", "front_door")),
                        "cohort": event.get("cohort", scenario.get("cohort", "ambiguous")),
                        "benchmark_kind": event.get("benchmark_kind", "action"),
                        "mode": mode,
                        "source": source,
                        "source_event_id": source_event_id,
                        "metadata": metadata,
                        "expected_event_context": expected_event_context,
                        "expect_memory_items": bool(memory.get("expected_to_help", False)),
                        "expect_duplicate_status": bool(event.get("expect_duplicate_status", False)),
                        "expected_status": event.get("expected_status"),
                        "repeated_request": bool(event.get("repeated_request", False)),
                        "direct_label": event.get("direct_label", str(event.get("label", scenario.get("scenario_family", "Synthetic Event")))),
                        "sequence_id": scenario.get("sequence_id", scenario_id),
                        "frame_index": event.get("frame_index"),
                        "time_of_day": variant.get("time_of_day", scenario.get("time_of_day", "day")),
                        "indoor_outdoor": scenario.get("indoor_outdoor", "outdoor"),
                        "scenario_type": event.get("scenario_type", scenario.get("scenario_type", "synthetic_event")),
                        "preferences_relevant": bool(memory.get("preference_tags")),
                        "memory_expected_to_help": bool(memory.get("expected_to_help", False)),
                        "quality_grade": scenario.get("quality_grade", "synthetic_reviewed"),
                        "source_type": scenario.get("source_type", "synthetic"),
                        "benchmark_eligibility": scenario.get("benchmark_eligibility", "review_only"),
                        "generated_from_case_id": generated_from_case_id,
                        "generator_type": scenario.get("generator_type", "scenario_catalog"),
                        "prompt_version": scenario.get("prompt_version", "v1"),
                        "augmentation_type": variant.get("weather", variant.get("fidelity", "base")),
                        "incident_id_expected": correlation.get("incident_id_expected", scenario_id),
                        "linked_event_ids_expected": list(correlation.get("linked_event_ids_expected", [])),
                        "cross_cam_correlation_expected": bool(correlation.get("cross_cam_correlation_expected", False)),
                        "escalation_expected": list(correlation.get("escalation_expected", [])),
                        "final_incident_action_expected": correlation.get("final_incident_action_expected"),
                    }
                )
    return expanded


def _load_manifest(path: pathlib.Path) -> list[EvalCase]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "scenarios" in raw:
        raw = _expand_scenario_catalog(raw, path)
    cases: list[EvalCase] = []
    for idx, item in enumerate(raw, start=1):
        image_path = item.get("image_path")
        image_url = item.get("image_url")
        if not image_path and not image_url:
            raise ValueError(f"Case {idx} must include image_path or image_url")

        suite = str(item.get("suite", "smoke_pipeline")).strip().lower()
        if suite not in ALL_SUITES:
            raise ValueError(f"Case {idx} suite must be one of {sorted(ALL_SUITES)}")
        source_mode = str(item.get("source_mode", "nvr_webhook")).strip().lower()
        if source_mode not in SOURCE_MODES:
            raise ValueError(f"Case {idx} source_mode must be one of {sorted(SOURCE_MODES)}")

        expected_action = str(item.get("expected_action", "suppress")).strip().lower()
        if expected_action not in {"alert", "suppress"}:
            raise ValueError(f"Case {idx} expected_action must be alert|suppress")
        expected_risk_level = str(item.get("expected_risk_level", _default_expected_risk(expected_action))).strip().lower()
        if expected_risk_level not in {"none", "low", "medium", "high"}:
            raise ValueError(f"Case {idx} expected_risk_level must be none|low|medium|high")

        cases.append(
            EvalCase(
                scenario_id=item.get("scenario_id", item.get("case_id", f"scenario_{idx}")),
                scenario_family=str(item.get("scenario_family", item.get("scenario_type", "routine"))),
                source_mode=source_mode,
                suite=suite,
                case_id=item.get("case_id", f"case_{idx}"),
                image_path=image_path,
                image_url=image_url,
                expected_action=expected_action,
                expected_risk_level=expected_risk_level,
                expected_visibility_policy=str(
                    item.get("expected_visibility_policy", _default_visibility(expected_risk_level))
                ),
                expected_notification_policy=str(
                    item.get("expected_notification_policy", _default_notification(expected_risk_level))
                ),
                cam_id=item.get("cam_id", f"eval_cam_{idx}"),
                home_id=item.get("home_id", "home"),
                zone=item.get("zone", "eval-zone"),
                cohort=str(item.get("cohort", "benign")).strip().lower(),
                benchmark_kind=str(item.get("benchmark_kind", "action")).strip().lower(),
                mode=str(item.get("mode", "canonical")).strip().lower(),
                source=item.get("source"),
                source_event_id=item.get("source_event_id"),
                metadata=dict(item.get("metadata", {})),
                expected_reasoning_short=str(item.get("expected_reasoning_short", "")),
                expected_event_context=dict(item.get("expected_event_context", {})),
                expect_memory_items=bool(item.get("expect_memory_items", False)),
                expect_duplicate_status=bool(item.get("expect_duplicate_status", False)),
                expected_status=item.get("expected_status"),
                repeated_request=bool(item.get("repeated_request", False)),
                direct_label=str(item.get("direct_label", "eval-frame")),
                sequence_id=item.get("sequence_id"),
                frame_index=item.get("frame_index"),
                time_of_day=str(item.get("time_of_day", "day")),
                indoor_outdoor=str(item.get("indoor_outdoor", "outdoor")),
                scenario_type=str(item.get("scenario_type", "routine")),
                preferences_relevant=bool(item.get("preferences_relevant", False)),
                memory_expected_to_help=bool(item.get("memory_expected_to_help", False)),
                quality_grade=str(item.get("quality_grade", "reviewed")),
                source_type=str(item.get("source_type", "manual")),
                benchmark_eligibility=str(item.get("benchmark_eligibility", "gating")),
                generated_from_case_id=item.get("generated_from_case_id"),
                generator_type=item.get("generator_type"),
                prompt_version=item.get("prompt_version"),
                augmentation_type=item.get("augmentation_type"),
                incident_id_expected=item.get("incident_id_expected"),
                linked_event_ids_expected=list(item.get("linked_event_ids_expected", [])),
                cross_cam_correlation_expected=bool(item.get("cross_cam_correlation_expected", False)),
                escalation_expected=list(item.get("escalation_expected", [])),
                final_incident_action_expected=item.get("final_incident_action_expected"),
            )
        )
    return cases


def _encode_image(path: pathlib.Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _resolve_image(case: EvalCase) -> tuple[str | None, str | None]:
    if case.image_path:
        image_file = pathlib.Path(case.image_path)
        if not image_file.is_absolute():
            image_file = (pathlib.Path(__file__).resolve().parent.parent / image_file).resolve()
        if not image_file.exists():
            raise FileNotFoundError(f"image not found: {image_file}")
        return _encode_image(image_file), None
    return None, case.image_url


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any], float]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            latency_s = time.perf_counter() - start
            return resp.status, body, latency_s
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        latency_s = time.perf_counter() - start
        return exc.code, {"detail": detail[:500]}, latency_s


def _fetch_events(base_url: str, api_key: str, stream_id: str) -> list[dict[str, Any]]:
    query_url = f"{base_url.rstrip('/')}/api/events?stream_id={stream_id}&limit=10"
    req = urllib.request.Request(
        query_url,
        headers={"x-api-key": api_key},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _repeat_source_event_id(case: EvalCase, repeat: int, run_id: str) -> str | None:
    if not case.source_event_id:
        return None
    base = f"{case.source_event_id}-{run_id}"
    if case.expect_duplicate_status or case.benchmark_kind == "idempotency":
        return base
    return f"{base}-r{repeat}"


def _post_case(
    base_url: str,
    api_key: str,
    case: EvalCase,
    repeat: int,
    memory_mode: str,
    run_id: str,
) -> EvalResult:
    try:
        image_b64, image_url = _resolve_image(case)
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            case=case,
            repeat=repeat,
            memory_mode=memory_mode,
            status_code=0,
            action="",
            risk_level="none",
            visibility_policy="hidden",
            notification_policy="none",
            ok=False,
            latency_s=0.0,
            reasoning_degraded=False,
            fallback_agent_count=0,
            context_ok=False,
            idempotency_ok=False,
            memory_present=False,
            error=str(exc),
        )

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "x-novin-memory": memory_mode,
        "x-novin-benchmark": "1",
    }
    source_event_id = _repeat_source_event_id(case, repeat, run_id)
    if case.mode == "direct_frame":
        payload: dict[str, Any] = {
            "b64_frame": image_b64,
            "stream_id": case.cam_id,
            "label": case.direct_label,
            "site_id": case.home_id,
            "zone": case.zone,
        }
        endpoint = "/api/novin/ingest/frame"
    else:
        payload = {
            "cam_id": case.cam_id,
            "home_id": case.home_id,
            "zone": case.zone,
            "metadata": case.metadata,
        }
        if case.source:
            payload["source"] = case.source
        if source_event_id:
            payload["source_event_id"] = source_event_id
        if image_b64:
            payload["image_b64"] = image_b64
        else:
            payload["image_url"] = image_url
        endpoint = "/api/novin/ingest"

    url = f"{base_url.rstrip('/')}{endpoint}"
    status_code, body, latency_s = _request_json(url, payload, headers)

    if case.repeated_request:
        status_code, body, latency_s = _request_json(url, payload, headers)

    response_status = str(body.get("status", "")) if isinstance(body, dict) else ""
    action = str(body.get("action", "")).strip().lower() if isinstance(body, dict) else ""
    risk_level = str(body.get("risk_level", "none")).strip().lower() if isinstance(body, dict) else "none"
    visibility_policy = str(body.get("visibility_policy", "hidden")).strip().lower() if isinstance(body, dict) else "hidden"
    notification_policy = str(body.get("notification_policy", "none")).strip().lower() if isinstance(body, dict) else "none"
    reasoning_degraded = bool(body.get("reasoning_degraded", False)) if isinstance(body, dict) else False
    outputs = body.get("agent_outputs", []) if isinstance(body, dict) else []
    fallback_agent_count = sum(
        1 for output in outputs if str(output.get("rationale", "")).startswith("Agent fallback:")
    )

    event_context = body.get("event_context", {}) if isinstance(body, dict) else {}
    consumer_summary = body.get("consumer_summary", {}) if isinstance(body, dict) else {}
    operator_summary = body.get("operator_summary", {}) if isinstance(body, dict) else {}
    evidence_digest = body.get("evidence_digest", []) if isinstance(body, dict) else []
    judgement = body.get("judgement", {}) if isinstance(body, dict) else {}
    routing_decision = body.get("routing_decision", {}) if isinstance(body, dict) else {}
    action_readiness = body.get("action_readiness", {}) if isinstance(body, dict) else {}
    explanation = judgement.get("evidence", {}) if isinstance(judgement, dict) else {}
    case_id = str(body.get("case_id", "")).strip() if isinstance(body, dict) else ""
    case_status = str(body.get("case_status", "")).strip().lower() if isinstance(body, dict) else ""
    ambiguity_state = str(body.get("ambiguity_state", "")).strip().lower() if isinstance(body, dict) else ""
    confidence_band = str(body.get("confidence_band", "")).strip().lower() if isinstance(body, dict) else ""
    case_fields_ok = bool(
        case_id
        and case_status in {"routine", "interesting", "watch", "verify", "urgent", "active_threat", "closed_benign"}
        and ambiguity_state in {"resolved", "monitoring", "ambiguous", "contested"}
        and confidence_band in {"low", "medium", "high"}
    )
    consumer_summary_ok = bool(
        isinstance(consumer_summary, dict)
        and str(consumer_summary.get("headline", "")).strip()
        and str(consumer_summary.get("reason", "")).strip()
        and str(consumer_summary.get("action_now", "")).strip()
    )
    operator_summary_ok = bool(
        isinstance(operator_summary, dict)
        and str(operator_summary.get("what_observed", "")).strip()
        and str(operator_summary.get("why_flagged", "")).strip()
        and str(operator_summary.get("why_not_benign", "")).strip()
        and str(operator_summary.get("what_is_uncertain", "")).strip()
        and str(operator_summary.get("timeline_context", "")).strip()
        and str(operator_summary.get("recommended_next_step", "")).strip()
    )
    evidence_digest_ok = bool(
        isinstance(evidence_digest, list)
        and len(evidence_digest) >= 3
        and all(
            isinstance(item, dict)
            and str(item.get("kind", "")).strip()
            and str(item.get("claim", "")).strip()
            and str(item.get("source", "")).strip()
            and str(item.get("status", "")).strip()
            for item in evidence_digest
        )
    )
    judgement_contract_ok = bool(
        isinstance(judgement, dict)
        and judgement.get("action") == action
        and judgement.get("risk_level") == risk_level
        and str(judgement.get("decision_rationale", "")).strip()
        and str(judgement.get("uncertainty_state", "")).strip() == case.expected_uncertainty_behavior
    )
    explanation_contract_ok = bool(
        isinstance(explanation, dict)
        and all(
            isinstance(explanation.get(field), list)
            for field in (
                "observed_evidence",
                "benign_evidence",
                "threat_evidence",
                "uncertainty_evidence",
                "routing_basis",
                "missing_information",
            )
        )
    )
    routing_contract_ok = bool(
        isinstance(routing_decision, dict)
        and routing_decision.get("visibility_policy") == visibility_policy
        and routing_decision.get("notification_policy") == notification_policy
        and str(routing_decision.get("storage_policy", "")).strip()
        and isinstance(routing_decision.get("delivery_targets"), list)
        and isinstance(routing_decision.get("routing_basis"), list)
    )
    expected_autonomy = case.expected_autonomy_eligibility or _default_autonomy_eligibility(
        case.expected_action,
        case.expected_risk_level,
    )
    action_readiness_ok = bool(
        isinstance(action_readiness, dict)
        and action_readiness.get("autonomy_eligible") == expected_autonomy
        and isinstance(action_readiness.get("allowed_action_types"), list)
        and isinstance(action_readiness.get("required_confirmations"), list)
        and isinstance(action_readiness.get("tool_targets"), list)
    )
    intelligence_quality_ok = (
        case_fields_ok
        and consumer_summary_ok
        and operator_summary_ok
        and evidence_digest_ok
        and judgement_contract_ok
        and explanation_contract_ok
        and routing_contract_ok
        and action_readiness_ok
    )

    context_ok = True
    for key, expected_value in case.expected_event_context.items():
        if key == "source_event_id" and source_event_id:
            expected_value = source_event_id
        if event_context.get(key) != expected_value:
            context_ok = False
            break

    idempotency_ok = True
    if case.expect_duplicate_status or case.benchmark_kind == "idempotency":
        idempotency_ok = response_status == "duplicate"

    memory_present = False
    if case.expect_memory_items and status_code == 200:
        try:
            events = _fetch_events(base_url, api_key, case.cam_id)
            if events:
                memory_present = bool(events[0].get("event_context"))
        except Exception:
            memory_present = False

    queued_error = response_status == "queued"
    valid_action = action in {"alert", "suppress"}
    status_ok = response_status == case.expected_status if case.expected_status else True
    action_ok = action == case.expected_action if valid_action else False
    risk_ok = risk_level == case.expected_risk_level
    visibility_ok = visibility_policy == case.expected_visibility_policy
    notification_ok = notification_policy == case.expected_notification_policy
    ok = (
        status_code == 200
        and not queued_error
        and status_ok
        and (action_ok or case.expect_duplicate_status or case.benchmark_kind == "idempotency")
        and risk_ok
        and visibility_ok
        and notification_ok
        and not reasoning_degraded
        and fallback_agent_count == 0
        and (context_ok or not case.expected_event_context)
        and idempotency_ok
        and intelligence_quality_ok
    )

    return EvalResult(
        case=case,
        repeat=repeat,
        memory_mode=memory_mode,
        status_code=status_code,
        action=action,
        risk_level=risk_level,
        visibility_policy=visibility_policy,
        notification_policy=notification_policy,
        ok=ok,
        latency_s=latency_s,
        reasoning_degraded=reasoning_degraded,
        fallback_agent_count=fallback_agent_count,
        context_ok=context_ok,
        idempotency_ok=idempotency_ok,
        memory_present=memory_present,
        case_fields_ok=case_fields_ok,
        consumer_summary_ok=consumer_summary_ok,
        operator_summary_ok=operator_summary_ok,
        evidence_digest_ok=evidence_digest_ok,
        judgement_contract_ok=judgement_contract_ok,
        explanation_contract_ok=explanation_contract_ok,
        routing_contract_ok=routing_contract_ok,
        action_readiness_ok=action_readiness_ok,
        intelligence_quality_ok=intelligence_quality_ok,
        error="queued async response" if queued_error else "",
        response_status=response_status,
        response_json=body if isinstance(body, dict) else {},
        benchmark_telemetry=body.get("benchmark_telemetry", {}) if isinstance(body, dict) else {},
    )


def _evaluate(
    base_url: str,
    api_key: str,
    cases: list[EvalCase],
    repeats: int,
    memory_mode: str,
    run_id: str,
) -> list[EvalResult]:
    results: list[EvalResult] = []
    for repeat in range(1, repeats + 1):
        for case in cases:
            results.append(_post_case(base_url, api_key, case, repeat, memory_mode, run_id))
    return results


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _quantile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _is_action_case(result: EvalResult) -> bool:
    return result.case.benchmark_kind != "idempotency" and result.action in {"alert", "suppress"}


def _cohort_accuracy(results: list[EvalResult]) -> float:
    action_results = [result for result in results if _is_action_case(result)]
    return _safe_div(
        sum(1 for result in action_results if result.action == result.case.expected_action),
        len(action_results),
    )


def _top_latency_cases(results: list[EvalResult], limit: int = 5) -> list[dict[str, Any]]:
    by_case: dict[str, list[float]] = defaultdict(list)
    for result in results:
        if result.latency_s > 0:
            by_case[result.case.case_id].append(result.latency_s)
    ranked = sorted(
        (
            {
                "case_id": case_id,
                "suite": next(result.case.suite for result in results if result.case.case_id == case_id),
                "max_latency_s": round(max(latencies), 4),
                "mean_latency_s": round(statistics.mean(latencies), 4),
                "samples": len(latencies),
            }
            for case_id, latencies in by_case.items()
        ),
        key=lambda item: item["max_latency_s"],
        reverse=True,
    )
    return ranked[:limit]


def _stage_latency_summary(results: list[EvalResult]) -> dict[str, Any]:
    stages = {
        "vision_latency_ms": [],
        "history_latency_ms": [],
        "reasoning_latency_ms": [],
        "pipeline_latency_ms": [],
    }
    reasoning_repairs = 0
    skipped_agents = 0
    for result in results:
        telemetry = result.benchmark_telemetry or {}
        for key in stages:
            value = telemetry.get(key)
            if isinstance(value, (int, float)) and value > 0:
                stages[key].append(float(value))
        reasoning_repairs += int(telemetry.get("reasoning_repairs", 0) or 0)
        skipped_agents += int(telemetry.get("reasoning_skipped_agents", 0) or 0)
    return {
        "stage_p50_ms": {
            key: round(_quantile(values, 0.50), 2) if values else 0.0 for key, values in stages.items()
        },
        "stage_p95_ms": {
            key: round(_quantile(values, 0.95), 2) if values else 0.0 for key, values in stages.items()
        },
        "repair_retry_count": reasoning_repairs,
        "skipped_agent_count": skipped_agents,
    }


def _failure_buckets(results: list[EvalResult]) -> dict[str, int]:
    buckets: dict[str, int] = defaultdict(int)
    for result in results:
        if result.status_code != 200:
            buckets["http_error"] += 1
        if result.response_status == "queued":
            buckets["queued_response"] += 1
        if result.reasoning_degraded:
            buckets["reasoning_degraded"] += 1
        if result.fallback_agent_count > 0:
            buckets["fallback_agents"] += 1
        if not result.case_fields_ok:
            buckets["case_fields_missing"] += 1
        if not result.consumer_summary_ok:
            buckets["consumer_summary_missing"] += 1
        if not result.operator_summary_ok:
            buckets["operator_summary_missing"] += 1
        if not result.evidence_digest_ok:
            buckets["evidence_digest_missing"] += 1
        if result.case.expected_event_context and not result.context_ok:
            buckets["context_propagation"] += 1
        if (result.case.expect_duplicate_status or result.case.benchmark_kind == "idempotency") and not result.idempotency_ok:
            buckets["idempotency"] += 1
        if _is_action_case(result) and result.action != result.case.expected_action:
            buckets["action_mismatch"] += 1
    return dict(sorted(buckets.items()))


def _suite_case_ids(results: list[EvalResult]) -> list[str]:
    return sorted({result.case.case_id for result in results})


def _build_metrics(cases: list[EvalCase], results: list[EvalResult], repeats: int) -> dict[str, Any]:
    total_attempts = len(results)
    passed = sum(1 for r in results if r.ok)
    action_attempts = [r for r in results if _is_action_case(r)]
    risk_attempts = [r for r in results if r.risk_level in {"none", "low", "medium", "high"}]
    accuracy_action = _safe_div(
        sum(1 for r in action_attempts if r.action == r.case.expected_action),
        len(action_attempts),
    )
    risk_level_accuracy = _safe_div(
        sum(1 for r in risk_attempts if r.risk_level == r.case.expected_risk_level),
        len(risk_attempts),
    )

    tp = sum(1 for r in action_attempts if r.case.expected_action == "alert" and r.action == "alert")
    fp = sum(1 for r in action_attempts if r.case.expected_action == "suppress" and r.action == "alert")
    fn = sum(1 for r in action_attempts if r.case.expected_action == "alert" and r.action != "alert")

    benign_attempts = [r for r in action_attempts if r.case.cohort == "benign"]
    threat_attempts = [r for r in action_attempts if r.case.cohort == "threat"]
    threat_risk_attempts = [r for r in risk_attempts if r.case.cohort == "threat"]
    entry_zone_attempts = [r for r in risk_attempts if r.case.zone in {"front_door", "porch", "garage", "back_door"}]
    tamper_attempts = [r for r in risk_attempts if r.case.scenario_family == "tamper" or "tamper" in r.case.scenario_type]
    context_attempts = [r for r in results if r.case.expected_event_context and r.case.benchmark_kind != "idempotency"]
    idempotency_attempts = [r for r in results if r.case.expect_duplicate_status or r.case.benchmark_kind == "idempotency"]
    memory_attempts = [r for r in results if r.case.expect_memory_items]
    visible_attempts = [r for r in risk_attempts if r.visibility_policy == r.case.expected_visibility_policy]
    notification_attempts = [r for r in risk_attempts if r.notification_policy == r.case.expected_notification_policy]
    judgement_attempts = [r for r in action_attempts if r.judgement_contract_ok]
    explanation_attempts = [r for r in results if r.explanation_contract_ok]
    routing_attempts = [r for r in risk_attempts if r.routing_contract_ok and r.visibility_policy == r.case.expected_visibility_policy and r.notification_policy == r.case.expected_notification_policy]
    readiness_attempts = [r for r in results if r.action_readiness_ok]

    cohort_metrics: dict[str, dict[str, float]] = {}
    for cohort in sorted({c.cohort for c in cases}):
        cohort_results = [r for r in results if r.case.cohort == cohort]
        cohort_metrics[cohort] = {
            "accuracy_action": _cohort_accuracy(cohort_results),
            "reasoning_degraded_rate": _safe_div(
                sum(1 for r in cohort_results if r.reasoning_degraded),
                len(cohort_results),
            ),
        }

    by_case: dict[str, set[str]] = defaultdict(set)
    for result in action_attempts:
        by_case[result.case.case_id].add(result.action)
    unstable_case_ids = sorted(case_id for case_id, actions in by_case.items() if len(actions) > 1)

    latencies = [r.latency_s for r in results if r.latency_s > 0]
    degraded_cases = sorted({r.case.case_id for r in results if r.reasoning_degraded})
    fallback_cases = sorted({r.case.case_id for r in results if r.fallback_agent_count > 0})

    return {
        "attempts_total": total_attempts,
        "attempts_passed": passed,
        "attempts_failed": total_attempts - passed,
        "accuracy_action": accuracy_action,
        "risk_level_accuracy": risk_level_accuracy,
        "judgement_accuracy": _safe_div(len(judgement_attempts), len(action_attempts)),
        "explanation_contract_accuracy": _safe_div(len(explanation_attempts), total_attempts),
        "routing_accuracy": _safe_div(len(routing_attempts), len(risk_attempts)),
        "autonomy_readiness_classification_accuracy": _safe_div(len(readiness_attempts), total_attempts),
        "alert_precision": _safe_div(tp, tp + fp),
        "alert_recall": _safe_div(tp, tp + fn),
        "high_risk_recall": _safe_div(
            sum(1 for r in threat_risk_attempts if r.risk_level == "high"),
            len(threat_risk_attempts),
        ),
        "medium_or_higher_recall": _safe_div(
            sum(1 for r in threat_risk_attempts if r.risk_level in {"medium", "high"}),
            len(threat_risk_attempts),
        ),
        "none_rate_on_true_threats": _safe_div(
            sum(1 for r in threat_risk_attempts if r.risk_level == "none"),
            len(threat_risk_attempts),
        ),
        "false_alert_rate_benign": _safe_div(sum(1 for r in benign_attempts if r.action == "alert"), len(benign_attempts)),
        "missed_alert_rate_threat": _safe_div(sum(1 for r in threat_attempts if r.action != "alert"), len(threat_attempts)),
        "show_policy_accuracy": _safe_div(len(visible_attempts), len(risk_attempts)),
        "notification_policy_accuracy": _safe_div(len(notification_attempts), len(risk_attempts)),
        "queued_response_rate": _safe_div(sum(1 for r in results if r.response_status == "queued"), total_attempts),
        "entry_zone_threat_recall": _safe_div(
            sum(1 for r in entry_zone_attempts if r.case.cohort == "threat" and r.risk_level in {"medium", "high"}),
            sum(1 for r in entry_zone_attempts if r.case.cohort == "threat"),
        ),
        "tamper_recall": _safe_div(
            sum(1 for r in tamper_attempts if r.risk_level in {"medium", "high"}),
            len(tamper_attempts),
        ),
        "reasoning_degraded_rate": _safe_div(sum(1 for r in results if r.reasoning_degraded), total_attempts),
        "fallback_agent_rate": _safe_div(sum(r.fallback_agent_count for r in results), total_attempts * 4),
        "case_fields_completeness_rate": _safe_div(sum(1 for r in results if r.case_fields_ok), total_attempts),
        "consumer_summary_rate": _safe_div(sum(1 for r in results if r.consumer_summary_ok), total_attempts),
        "operator_summary_rate": _safe_div(sum(1 for r in results if r.operator_summary_ok), total_attempts),
        "evidence_digest_rate": _safe_div(sum(1 for r in results if r.evidence_digest_ok), total_attempts),
        "intelligence_quality_rate": _safe_div(sum(1 for r in results if r.intelligence_quality_ok), total_attempts),
        "http_error_rate": _safe_div(sum(1 for r in results if r.status_code != 200), total_attempts),
        "context_propagation_rate": _safe_div(sum(1 for r in context_attempts if r.context_ok), len(context_attempts)),
        "idempotency_correctness_rate": _safe_div(sum(1 for r in idempotency_attempts if r.idempotency_ok), len(idempotency_attempts)),
        "memory_presence_rate": _safe_div(sum(1 for r in memory_attempts if r.memory_present), len(memory_attempts)),
        "flip_rate": _safe_div(len(unstable_case_ids), len(cases)),
        "p50_latency_s": round(_quantile(latencies, 0.50), 4),
        "p95_latency_s": round(_quantile(latencies, 0.95), 4),
        "mean_latency_s": round(statistics.mean(latencies), 4) if latencies else 0.0,
        "run_accuracy": [
            _safe_div(
                sum(1 for r in results if r.repeat == rep and _is_action_case(r) and r.action == r.case.expected_action),
                sum(1 for r in results if r.repeat == rep and _is_action_case(r)),
            )
            for rep in range(1, repeats + 1)
        ],
        "cohort_metrics": cohort_metrics,
        "unstable_case_ids": unstable_case_ids,
        "degraded_case_ids": degraded_cases,
        "fallback_case_ids": fallback_cases,
        "top_latency_cases": _top_latency_cases(results),
        "stage_latency_summary": _stage_latency_summary(results),
        "failure_buckets": _failure_buckets(results),
        "context_cases": sorted({r.case.case_id for r in context_attempts}),
        "idempotency_cases": sorted({r.case.case_id for r in idempotency_attempts}),
        "suite_case_ids": _suite_case_ids(results),
    }


def _build_group_metrics(
    cases: list[EvalCase],
    results: list[EvalResult],
    repeats: int,
    attr: str,
    values: set[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for value in sorted(values):
        group_cases = [case for case in cases if getattr(case, attr) == value]
        group_results = [result for result in results if getattr(result.case, attr) == value]
        metrics = _build_metrics(group_cases, group_results, repeats) if group_cases else _empty_metrics(repeats)
        if attr == "source_mode":
            metrics["normalization_correctness"] = metrics["context_propagation_rate"]
        payload[value] = metrics
    return payload


def _build_incident_metrics(results: list[EvalResult]) -> dict[str, Any]:
    grouped: dict[str, list[EvalResult]] = defaultdict(list)
    for result in results:
        grouped[result.case.scenario_id].append(result)

    incidents = 0
    final_action_correct = 0
    thread_correct = 0
    cross_cam_expected = 0
    cross_cam_correct = 0
    escalation_expected = 0
    escalation_correct = 0
    duplicate_expected = 0
    duplicate_correct = 0
    threading_failures: list[str] = []
    cross_cam_failures: list[str] = []
    escalation_failures: list[str] = []

    for scenario_id, scenario_results in grouped.items():
        incidents += 1
        ordered = sorted(
            scenario_results,
            key=lambda result: (
                result.case.sequence_id or result.case.scenario_id,
                result.case.frame_index if result.case.frame_index is not None else 10_000,
                result.case.case_id,
                result.repeat,
            ),
        )
        expected_case_id = ordered[0].case.incident_id_expected or scenario_id
        actual_case_ids = {
            str(result.response_json.get("case_id", "")).strip()
            for result in ordered
            if isinstance(result.response_json, dict) and str(result.response_json.get("case_id", "")).strip()
        }
        expected_final = ordered[0].case.final_incident_action_expected
        actual_final = next((result.action for result in reversed(ordered) if result.action in {"alert", "suppress"}), "")
        if expected_final and actual_final == expected_final:
            final_action_correct += 1

        thread_ok = all(result.context_ok and result.idempotency_ok for result in ordered)
        if expected_case_id:
            thread_ok = thread_ok and actual_case_ids == {expected_case_id}
        if thread_ok:
            thread_correct += 1
        else:
            threading_failures.append(scenario_id)

        if ordered[0].case.cross_cam_correlation_expected:
            cross_cam_expected += 1
            scenario_cams = {result.case.cam_id for result in ordered}
            if len(scenario_cams) > 1 and thread_ok and len(actual_case_ids) == 1:
                cross_cam_correct += 1
            else:
                cross_cam_failures.append(scenario_id)

        expected_escalation = ordered[0].case.escalation_expected
        if expected_escalation:
            escalation_expected += 1
            actual_escalation = [result.action for result in ordered if result.action in {"alert", "suppress"}]
            if actual_escalation[: len(expected_escalation)] == expected_escalation:
                escalation_correct += 1
            else:
                escalation_failures.append(scenario_id)

        duplicate_results = [result for result in ordered if result.case.expect_duplicate_status]
        if duplicate_results:
            duplicate_expected += 1
            if all(result.idempotency_ok for result in duplicate_results):
                duplicate_correct += 1

    return {
        "incident_count": incidents,
        "incident_final_action_accuracy": _safe_div(final_action_correct, incidents),
        "incident_final_outcome_accuracy": _safe_div(final_action_correct, incidents),
        "event_thread_accuracy": _safe_div(thread_correct, incidents),
        "cross_cam_correlation_rate": _safe_div(cross_cam_correct, cross_cam_expected),
        "incident_escalation_accuracy": _safe_div(escalation_correct, escalation_expected),
        "duplicate_redelivery_correctness": _safe_div(duplicate_correct, duplicate_expected),
        "threading_failures": sorted(threading_failures),
        "cross_cam_failures": sorted(cross_cam_failures),
        "escalation_failures": sorted(escalation_failures),
    }


def _memory_help_rate(results_off: list[EvalResult], results_on: list[EvalResult]) -> float:
    indexed_off = {(result.case.case_id, result.repeat): result for result in results_off}
    indexed_on = {(result.case.case_id, result.repeat): result for result in results_on}
    candidates = [
        key
        for key, result in indexed_on.items()
        if result.case.memory_expected_to_help and key in indexed_off
    ]
    if not candidates:
        return 0.0
    helped = 0
    for key in candidates:
        off = indexed_off[key]
        on = indexed_on[key]
        off_correct = off.action == off.case.expected_action and not off.reasoning_degraded
        on_correct = on.action == on.case.expected_action and not on.reasoning_degraded
        if on_correct and not off_correct:
            helped += 1
    return _safe_div(helped, len(candidates))


def _compare_memory(metrics_off: dict[str, Any], metrics_on: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "accuracy_action",
        "false_alert_rate_benign",
        "missed_alert_rate_threat",
        "reasoning_degraded_rate",
        "flip_rate",
    ]
    delta = {key: metrics_on[key] - metrics_off[key] for key in keys}
    return {
        "off": {key: metrics_off[key] for key in keys},
        "on": {key: metrics_on[key] for key in keys},
        "delta": delta,
    }


def _suite_counts(cases: list[EvalCase]) -> dict[str, int]:
    counts: dict[str, int] = {suite: 0 for suite in sorted(ALL_SUITES)}
    for case in cases:
        counts[case.suite] += 1
    return dict(counts)


def _filter_cases(cases: list[EvalCase], *, suite: str | None = None, eligibility: str | None = None) -> list[EvalCase]:
    filtered = cases
    if suite is not None:
        filtered = [case for case in filtered if case.suite == suite]
    if eligibility is not None:
        filtered = [case for case in filtered if case.benchmark_eligibility == eligibility]
    return filtered


def _filter_results(
    results: list[EvalResult],
    *,
    suite: str | None = None,
    eligibility: str | None = None,
) -> list[EvalResult]:
    filtered = results
    if suite is not None:
        filtered = [result for result in filtered if result.case.suite == suite]
    if eligibility is not None:
        filtered = [result for result in filtered if result.case.benchmark_eligibility == eligibility]
    return filtered


def _empty_metrics(repeats: int) -> dict[str, Any]:
    return _build_metrics([], [], repeats)


def _build_suite_metrics(cases: list[EvalCase], results: list[EvalResult], repeats: int) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for suite in sorted(ALL_SUITES):
        suite_cases = _filter_cases(cases, suite=suite)
        suite_results = _filter_results(results, suite=suite)
        payload[suite] = {
            "all_cases": _build_metrics(suite_cases, suite_results, repeats) if suite_cases else _empty_metrics(repeats),
            "gating_cases": _build_metrics(
                _filter_cases(suite_cases, eligibility="gating"),
                _filter_results(suite_results, eligibility="gating"),
                repeats,
            )
            if suite_cases
            else _empty_metrics(repeats),
            "quality_review_cases": _build_metrics(
                _filter_cases(suite_cases, eligibility="review_only"),
                _filter_results(suite_results, eligibility="review_only"),
                repeats,
            )
            if suite_cases
            else _empty_metrics(repeats),
        }
    return payload


def _suite_gate_results(
    suite_metrics_by_mode: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    for suite in sorted(suite_metrics_by_mode["off"].keys()):
        is_primary = suite == PRIMARY_GATE_SUITE
        mode_results = {}
        for mode in ("off", "on"):
            metrics = suite_metrics_by_mode[mode][suite]["gating_cases"]
            failures: list[str] = []
            if is_primary:
                if metrics["risk_level_accuracy"] < args.min_risk_level_accuracy:
                    failures.append("min_risk_level_accuracy")
                if metrics["medium_or_higher_recall"] < args.min_medium_or_higher_recall:
                    failures.append("min_medium_or_higher_recall")
                if metrics["none_rate_on_true_threats"] > args.max_none_rate_on_true_threats:
                    failures.append("max_none_rate_on_true_threats")
                if metrics["show_policy_accuracy"] < args.min_show_policy_accuracy:
                    failures.append("min_show_policy_accuracy")
                if metrics["notification_policy_accuracy"] < args.min_notification_policy_accuracy:
                    failures.append("min_notification_policy_accuracy")
                if metrics["case_fields_completeness_rate"] < args.min_case_fields_completeness_rate:
                    failures.append("min_case_fields_completeness_rate")
                if metrics["consumer_summary_rate"] < args.min_consumer_summary_rate:
                    failures.append("min_consumer_summary_rate")
                if metrics["operator_summary_rate"] < args.min_operator_summary_rate:
                    failures.append("min_operator_summary_rate")
                if metrics["evidence_digest_rate"] < args.min_evidence_digest_rate:
                    failures.append("min_evidence_digest_rate")
                if metrics["judgement_accuracy"] < args.min_judgement_accuracy:
                    failures.append("min_judgement_accuracy")
                if metrics["explanation_contract_accuracy"] < args.min_explanation_contract_accuracy:
                    failures.append("min_explanation_contract_accuracy")
                if metrics["routing_accuracy"] < args.min_routing_accuracy:
                    failures.append("min_routing_accuracy")
                if metrics["autonomy_readiness_classification_accuracy"] < args.min_autonomy_readiness_classification_accuracy:
                    failures.append("min_autonomy_readiness_classification_accuracy")
                if metrics["intelligence_quality_rate"] < args.min_intelligence_quality_rate:
                    failures.append("min_intelligence_quality_rate")
                if metrics["entry_zone_threat_recall"] < args.min_entry_zone_threat_recall:
                    failures.append("min_entry_zone_threat_recall")
                if metrics["tamper_recall"] < args.min_tamper_recall:
                    failures.append("min_tamper_recall")
                if metrics["queued_response_rate"] > args.max_queued_response_rate:
                    failures.append("max_queued_response_rate")
                if metrics["accuracy_action"] < args.min_action_accuracy:
                    failures.append("min_action_accuracy")
                if metrics["alert_recall"] < args.min_alert_recall:
                    failures.append("min_alert_recall")
                if metrics["false_alert_rate_benign"] > args.max_false_alert_rate_benign:
                    failures.append("max_false_alert_rate_benign")
                if metrics["missed_alert_rate_threat"] > args.max_missed_alert_rate_threat:
                    failures.append("max_missed_alert_rate_threat")
                if metrics["reasoning_degraded_rate"] > args.max_reasoning_degraded_rate:
                    failures.append("max_reasoning_degraded_rate")
                if metrics["fallback_agent_rate"] > args.max_fallback_agent_rate:
                    failures.append("max_fallback_agent_rate")
                if metrics["http_error_rate"] > args.max_http_error_rate:
                    failures.append("max_http_error_rate")
                if metrics["context_propagation_rate"] < args.min_context_propagation_rate:
                    failures.append("min_context_propagation_rate")
                if metrics["idempotency_correctness_rate"] < args.min_idempotency_correctness_rate:
                    failures.append("min_idempotency_correctness_rate")
                if metrics["flip_rate"] > args.max_flip_rate:
                    failures.append("max_flip_rate")
                if metrics["p95_latency_s"] > args.max_p95_latency_s:
                    failures.append("max_p95_latency_s")
            mode_results[mode] = {
                "hard_gate": is_primary,
                "pass": not failures,
                "failures": failures,
                "gating_case_count": len(metrics["suite_case_ids"]),
            }
        gates[suite] = mode_results
    return gates


def _evidence_tier_summary(suite_metrics: dict[str, Any]) -> dict[str, str]:
    return {
        PRIMARY_GATE_SUITE: "Primary pilot gate. This suite alone can justify outreach readiness.",
        "synthetic_home": "Secondary coverage suite. Use for scenario expansion and regression detection, not pilot authority.",
        "public_surveillance": "Secondary stress suite. Use for anomaly/crime robustness only, not home-security truth claims.",
        "smoke_pipeline": "Smoke suite. Use for pipeline sanity and contract checks only.",
    }


def _pilot_verdict(report: dict[str, Any]) -> str:
    primary = report["suite_gate_results"].get(PRIMARY_GATE_SUITE, {})
    primary_count = primary.get("off", {}).get("gating_case_count", 0)
    if primary_count == 0:
        return "not pilot-ready; staged real benchmark dataset missing"
    off_ok = primary.get("off", {}).get("pass", False)
    on_ok = primary.get("on", {}).get("pass", False)
    if off_ok and on_ok:
        return "pilot-ready based on staged real benchmark"
    return "not pilot-ready; staged real benchmark gate unmet"


def _print_report(report: dict[str, Any]) -> None:
    print("=== Novin Home Validation Ladder ===")
    print(
        f"pilot_readiness_verdict={report['pilot_readiness_verdict']} "
        f"release_verdict={report['release_verdict']} overall_pass={report['overall_pass']}"
    )
    for mode in ("off", "on"):
        metrics = report["metrics"][mode]
        print(
            f"memory={mode} risk_level_accuracy={metrics['risk_level_accuracy']:.1%} "
            f"judgement_accuracy={metrics['judgement_accuracy']:.1%} "
            f"explanation_contract_accuracy={metrics['explanation_contract_accuracy']:.1%} "
            f"routing_accuracy={metrics['routing_accuracy']:.1%} "
            f"autonomy_readiness_classification_accuracy={metrics['autonomy_readiness_classification_accuracy']:.1%} "
            f"medium_or_higher_recall={metrics['medium_or_higher_recall']:.1%} "
            f"show_policy_accuracy={metrics['show_policy_accuracy']:.1%} "
            f"notification_policy_accuracy={metrics['notification_policy_accuracy']:.1%} "
            f"case_fields_completeness_rate={metrics['case_fields_completeness_rate']:.1%} "
            f"consumer_summary_rate={metrics['consumer_summary_rate']:.1%} "
            f"operator_summary_rate={metrics['operator_summary_rate']:.1%} "
            f"evidence_digest_rate={metrics['evidence_digest_rate']:.1%} "
            f"intelligence_quality_rate={metrics['intelligence_quality_rate']:.1%} "
            f"none_rate_on_true_threats={metrics['none_rate_on_true_threats']:.1%} "
            f"accuracy_action={metrics['accuracy_action']:.1%} "
            f"alert_recall={metrics['alert_recall']:.1%} false_alert_rate_benign={metrics['false_alert_rate_benign']:.1%} "
            f"reasoning_degraded_rate={metrics['reasoning_degraded_rate']:.1%} flip_rate={metrics['flip_rate']:.1%} "
            f"p95_latency_s={metrics['p95_latency_s']:.3f}"
        )
        incident = report["incident_metrics"][mode]
        print(
            f"memory={mode} incident_final_action_accuracy={incident['incident_final_action_accuracy']:.1%} "
            f"event_thread_accuracy={incident['event_thread_accuracy']:.1%} "
            f"cross_cam_correlation_rate={incident['cross_cam_correlation_rate']:.1%} "
            f"incident_escalation_accuracy={incident['incident_escalation_accuracy']:.1%}"
        )
        for suite, suite_metrics in report["suite_metrics"][mode].items():
            gating = suite_metrics["gating_cases"]
            print(
                f"memory={mode} suite={suite} gating_cases={len(gating['suite_case_ids'])} "
                f"accuracy={gating['accuracy_action']:.1%} p95={gating['p95_latency_s']:.3f}"
            )
        for source_mode, source_metrics in report["source_mode_metrics"][mode].items():
            print(
                f"memory={mode} source_mode={source_mode} attempts={source_metrics['attempts_total']} "
                f"accuracy={source_metrics['accuracy_action']:.1%} normalization={source_metrics['context_propagation_rate']:.1%}"
            )
    print("memory_delta=" + json.dumps(report["memory_comparison"]["delta"], sort_keys=True))
    print(f"memory_help_rate={report['memory_help_rate']:.1%}")
    if report["gate_failures"]:
        for failure in report["gate_failures"]:
            print(f"gate_failure={failure}")


def _write_report_json(report_path: pathlib.Path, payload: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2))


def _check_gates(cases: list[EvalCase], report: dict[str, Any], args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    cohort_counts: dict[str, int] = defaultdict(int)
    for case in cases:
        cohort_counts[case.cohort] += 1

    if len(cases) < args.min_total_cases:
        failures.append(f"min_total_cases unmet: {len(cases)} < {args.min_total_cases}")
    for cohort in ("benign", "threat", "ambiguous", "temporal", "context", "ops"):
        if cohort_counts.get(cohort, 0) < getattr(args, f"min_{cohort}_cases"):
            failures.append(
                f"min_{cohort}_cases unmet: {cohort_counts.get(cohort, 0)} < {getattr(args, f'min_{cohort}_cases')}"
            )

    primary_off = report["suite_metrics"]["off"][PRIMARY_GATE_SUITE]["gating_cases"]
    primary_on = report["suite_metrics"]["on"][PRIMARY_GATE_SUITE]["gating_cases"]
    if not primary_off["suite_case_ids"]:
        failures.append("staged_real_home dataset missing: no primary pilot-gate cases")
    for mode, metrics in (("off", primary_off), ("on", primary_on)):
        if metrics["risk_level_accuracy"] < args.min_risk_level_accuracy:
            failures.append(f"{mode}: min_risk_level_accuracy unmet")
        if metrics["medium_or_higher_recall"] < args.min_medium_or_higher_recall:
            failures.append(f"{mode}: min_medium_or_higher_recall unmet")
        if metrics["none_rate_on_true_threats"] > args.max_none_rate_on_true_threats:
            failures.append(f"{mode}: max_none_rate_on_true_threats unmet")
        if metrics["show_policy_accuracy"] < args.min_show_policy_accuracy:
            failures.append(f"{mode}: min_show_policy_accuracy unmet")
        if metrics["notification_policy_accuracy"] < args.min_notification_policy_accuracy:
            failures.append(f"{mode}: min_notification_policy_accuracy unmet")
        if metrics["case_fields_completeness_rate"] < args.min_case_fields_completeness_rate:
            failures.append(f"{mode}: min_case_fields_completeness_rate unmet")
        if metrics["consumer_summary_rate"] < args.min_consumer_summary_rate:
            failures.append(f"{mode}: min_consumer_summary_rate unmet")
        if metrics["operator_summary_rate"] < args.min_operator_summary_rate:
            failures.append(f"{mode}: min_operator_summary_rate unmet")
        if metrics["evidence_digest_rate"] < args.min_evidence_digest_rate:
            failures.append(f"{mode}: min_evidence_digest_rate unmet")
        if metrics["judgement_accuracy"] < args.min_judgement_accuracy:
            failures.append(f"{mode}: min_judgement_accuracy unmet")
        if metrics["explanation_contract_accuracy"] < args.min_explanation_contract_accuracy:
            failures.append(f"{mode}: min_explanation_contract_accuracy unmet")
        if metrics["routing_accuracy"] < args.min_routing_accuracy:
            failures.append(f"{mode}: min_routing_accuracy unmet")
        if metrics["autonomy_readiness_classification_accuracy"] < args.min_autonomy_readiness_classification_accuracy:
            failures.append(f"{mode}: min_autonomy_readiness_classification_accuracy unmet")
        if metrics["intelligence_quality_rate"] < args.min_intelligence_quality_rate:
            failures.append(f"{mode}: min_intelligence_quality_rate unmet")
        if metrics["entry_zone_threat_recall"] < args.min_entry_zone_threat_recall:
            failures.append(f"{mode}: min_entry_zone_threat_recall unmet")
        if metrics["tamper_recall"] < args.min_tamper_recall:
            failures.append(f"{mode}: min_tamper_recall unmet")
        if metrics["queued_response_rate"] > args.max_queued_response_rate:
            failures.append(f"{mode}: max_queued_response_rate unmet")
        if metrics["accuracy_action"] < args.min_action_accuracy:
            failures.append(f"{mode}: min_action_accuracy unmet")
        if metrics["alert_recall"] < args.min_alert_recall:
            failures.append(f"{mode}: min_alert_recall unmet")
        if metrics["false_alert_rate_benign"] > args.max_false_alert_rate_benign:
            failures.append(f"{mode}: max_false_alert_rate_benign unmet")
        if metrics["missed_alert_rate_threat"] > args.max_missed_alert_rate_threat:
            failures.append(f"{mode}: max_missed_alert_rate_threat unmet")
        if metrics["reasoning_degraded_rate"] > args.max_reasoning_degraded_rate:
            failures.append(f"{mode}: max_reasoning_degraded_rate unmet")
        if metrics["fallback_agent_rate"] > args.max_fallback_agent_rate:
            failures.append(f"{mode}: max_fallback_agent_rate unmet")
        if metrics["http_error_rate"] > args.max_http_error_rate:
            failures.append(f"{mode}: max_http_error_rate unmet")
        if metrics["context_propagation_rate"] < args.min_context_propagation_rate:
            failures.append(f"{mode}: min_context_propagation_rate unmet")
        if metrics["idempotency_correctness_rate"] < args.min_idempotency_correctness_rate:
            failures.append(f"{mode}: min_idempotency_correctness_rate unmet")
        if metrics["flip_rate"] > args.max_flip_rate:
            failures.append(f"{mode}: max_flip_rate unmet")
        if metrics["p95_latency_s"] > args.max_p95_latency_s:
            failures.append(f"{mode}: max_p95_latency_s unmet")

    delta = report["memory_comparison"]["delta"]
    if delta["accuracy_action"] < -args.max_memory_accuracy_regression:
        failures.append("memory_on regressed action accuracy")
    if delta["false_alert_rate_benign"] > args.max_memory_false_alert_regression:
        failures.append("memory_on regressed false alert rate")
    if delta["missed_alert_rate_threat"] > args.max_memory_missed_alert_regression:
        failures.append("memory_on regressed missed alert rate")
    if delta["reasoning_degraded_rate"] > args.max_memory_reasoning_degraded_regression:
        failures.append("memory_on regressed degraded reasoning rate")
    if delta["flip_rate"] > args.max_memory_flip_regression:
        failures.append("memory_on regressed flip rate")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validation ladder benchmark for Novin Home")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="test-ingest-key")
    parser.add_argument("--manifest", default="test/fixtures/eval/home_security/home_security_validation_manifest.json")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--report-json", default="test/reports/deployment_benchmark_report.json")
    parser.add_argument("--min-total-cases", type=int, default=12)
    parser.add_argument("--min-benign-cases", type=int, default=3)
    parser.add_argument("--min-threat-cases", type=int, default=2)
    parser.add_argument("--min-ambiguous-cases", type=int, default=2)
    parser.add_argument("--min-temporal-cases", type=int, default=1)
    parser.add_argument("--min-context-cases", type=int, default=2)
    parser.add_argument("--min-ops-cases", type=int, default=2)
    parser.add_argument("--min-action-accuracy", type=float, default=0.70)
    parser.add_argument("--min-alert-recall", type=float, default=0.50)
    parser.add_argument("--min-risk-level-accuracy", type=float, default=0.70)
    parser.add_argument("--min-medium-or-higher-recall", type=float, default=0.70)
    parser.add_argument("--max-none-rate-on-true-threats", type=float, default=0.10)
    parser.add_argument("--min-show-policy-accuracy", type=float, default=0.80)
    parser.add_argument("--min-notification-policy-accuracy", type=float, default=0.80)
    parser.add_argument("--min-case-fields-completeness-rate", type=float, default=1.0)
    parser.add_argument("--min-consumer-summary-rate", type=float, default=0.95)
    parser.add_argument("--min-operator-summary-rate", type=float, default=0.95)
    parser.add_argument("--min-evidence-digest-rate", type=float, default=0.95)
    parser.add_argument("--min-judgement-accuracy", type=float, default=0.95)
    parser.add_argument("--min-explanation-contract-accuracy", type=float, default=0.95)
    parser.add_argument("--min-routing-accuracy", type=float, default=0.90)
    parser.add_argument("--min-autonomy-readiness-classification-accuracy", type=float, default=0.90)
    parser.add_argument("--min-intelligence-quality-rate", type=float, default=0.95)
    parser.add_argument("--min-entry-zone-threat-recall", type=float, default=0.70)
    parser.add_argument("--min-tamper-recall", type=float, default=0.70)
    parser.add_argument("--max-queued-response-rate", type=float, default=0.0)
    parser.add_argument("--max-false-alert-rate-benign", type=float, default=0.35)
    parser.add_argument("--max-missed-alert-rate-threat", type=float, default=0.50)
    parser.add_argument("--max-reasoning-degraded-rate", type=float, default=0.10)
    parser.add_argument("--max-fallback-agent-rate", type=float, default=0.05)
    parser.add_argument("--max-http-error-rate", type=float, default=0.0)
    parser.add_argument("--min-context-propagation-rate", type=float, default=0.95)
    parser.add_argument("--min-idempotency-correctness-rate", type=float, default=1.0)
    parser.add_argument("--max-flip-rate", type=float, default=0.20)
    parser.add_argument("--max-p95-latency-s", type=float, default=2.5)
    parser.add_argument("--max-memory-accuracy-regression", type=float, default=0.0)
    parser.add_argument("--max-memory-false-alert-regression", type=float, default=0.0)
    parser.add_argument("--max-memory-missed-alert-regression", type=float, default=0.0)
    parser.add_argument("--max-memory-reasoning-degraded-regression", type=float, default=0.0)
    parser.add_argument("--max-memory-flip-regression", type=float, default=0.0)
    args = parser.parse_args()

    manifest_path = pathlib.Path(args.manifest).expanduser().resolve()
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    cases = _load_manifest(manifest_path)
    repeats = max(1, args.repeats)
    run_id = uuid.uuid4().hex[:8]

    metrics_by_mode: dict[str, dict[str, Any]] = {}
    results_by_mode: dict[str, list[EvalResult]] = {}
    suite_metrics_by_mode: dict[str, dict[str, Any]] = {}
    source_mode_metrics_by_mode: dict[str, dict[str, Any]] = {}
    scenario_family_metrics_by_mode: dict[str, dict[str, Any]] = {}
    incident_metrics_by_mode: dict[str, dict[str, Any]] = {}
    for memory_mode in ("off", "on"):
        results = _evaluate(
            args.base_url,
            args.api_key,
            cases,
            repeats=repeats,
            memory_mode=memory_mode,
            run_id=f"{run_id}-{memory_mode}",
        )
        metrics_by_mode[memory_mode] = _build_metrics(cases, results, repeats=repeats)
        suite_metrics_by_mode[memory_mode] = _build_suite_metrics(cases, results, repeats=repeats)
        source_mode_metrics_by_mode[memory_mode] = _build_group_metrics(
            cases,
            results,
            repeats,
            "source_mode",
            SOURCE_MODES,
        )
        scenario_family_metrics_by_mode[memory_mode] = _build_group_metrics(
            cases,
            results,
            repeats,
            "scenario_family",
            {case.scenario_family for case in cases},
        )
        incident_metrics_by_mode[memory_mode] = _build_incident_metrics(results)
        results_by_mode[memory_mode] = results

    primary_off = suite_metrics_by_mode["off"][PRIMARY_GATE_SUITE]["gating_cases"]
    primary_on = suite_metrics_by_mode["on"][PRIMARY_GATE_SUITE]["gating_cases"]
    memory_comparison = _compare_memory(primary_off, primary_on)

    report = {
        "overall_pass": False,
        "release_verdict": "block",
        "pilot_readiness_verdict": "not pilot-ready; staged real benchmark gate unmet",
        "thresholds": vars(args),
        "run_id": run_id,
        "suite_counts": _suite_counts(cases),
        "evidence_tier_summary": _evidence_tier_summary(suite_metrics_by_mode["off"]),
        "metrics": metrics_by_mode,
        "suite_metrics": suite_metrics_by_mode,
        "source_mode_metrics": source_mode_metrics_by_mode,
        "normalization_correctness_by_source_mode": {
            mode: {
                source_mode: metrics["normalization_correctness"]
                for source_mode, metrics in source_mode_metrics_by_mode[mode].items()
            }
            for mode in ("off", "on")
        },
        "scenario_family_metrics": scenario_family_metrics_by_mode,
        "incident_metrics": incident_metrics_by_mode,
        "cohort_metrics": {mode: metrics_by_mode[mode]["cohort_metrics"] for mode in ("off", "on")},
        "suite_gate_results": {},
        "memory_comparison": memory_comparison,
        "memory_help_rate": _memory_help_rate(results_by_mode["off"], results_by_mode["on"]),
        "cross_cam_failures": {
            mode: incident_metrics_by_mode[mode]["cross_cam_failures"] for mode in ("off", "on")
        },
        "threading_failures": {
            mode: incident_metrics_by_mode[mode]["threading_failures"] for mode in ("off", "on")
        },
        "escalation_failures": {
            mode: incident_metrics_by_mode[mode]["escalation_failures"] for mode in ("off", "on")
        },
        "case_failures": [
            {
                "memory_mode": mode,
                "suite": result.case.suite,
                "source_mode": result.case.source_mode,
                "scenario_id": result.case.scenario_id,
                "scenario_family": result.case.scenario_family,
                "case_id": result.case.case_id,
                "benchmark_kind": result.case.benchmark_kind,
                "repeat": result.repeat,
                "status_code": result.status_code,
                "expected_action": result.case.expected_action,
                "expected_risk_level": result.case.expected_risk_level,
                "expected_visibility_policy": result.case.expected_visibility_policy,
                "expected_notification_policy": result.case.expected_notification_policy,
                "action": result.action,
                "risk_level": result.risk_level,
                "visibility_policy": result.visibility_policy,
                "notification_policy": result.notification_policy,
                "reasoning_degraded": result.reasoning_degraded,
                "fallback_agent_count": result.fallback_agent_count,
                "case_fields_ok": result.case_fields_ok,
                "consumer_summary_ok": result.consumer_summary_ok,
                "operator_summary_ok": result.operator_summary_ok,
                "evidence_digest_ok": result.evidence_digest_ok,
                "intelligence_quality_ok": result.intelligence_quality_ok,
                "latency_s": round(result.latency_s, 4),
                "error": result.error,
                "failure_buckets": _failure_buckets([result]),
            }
            for mode, results in results_by_mode.items()
            for result in results
            if not result.ok
        ],
        "results": {
            mode: [
                {
                    "repeat": result.repeat,
                    "memory_mode": result.memory_mode,
                    "suite": result.case.suite,
                    "source_mode": result.case.source_mode,
                    "scenario_id": result.case.scenario_id,
                    "scenario_family": result.case.scenario_family,
                    "case_id": result.case.case_id,
                    "cohort": result.case.cohort,
                    "benchmark_kind": result.case.benchmark_kind,
                    "expected_action": result.case.expected_action,
                    "expected_risk_level": result.case.expected_risk_level,
                    "actual_action": result.action,
                    "risk_level": result.risk_level,
                    "visibility_policy": result.visibility_policy,
                    "notification_policy": result.notification_policy,
                    "status_code": result.status_code,
                    "latency_s": round(result.latency_s, 4),
                    "reasoning_degraded": result.reasoning_degraded,
                    "fallback_agent_count": result.fallback_agent_count,
                    "context_ok": result.context_ok,
                    "idempotency_ok": result.idempotency_ok,
                    "memory_present": result.memory_present,
                    "case_fields_ok": result.case_fields_ok,
                    "consumer_summary_ok": result.consumer_summary_ok,
                    "operator_summary_ok": result.operator_summary_ok,
                    "evidence_digest_ok": result.evidence_digest_ok,
                    "intelligence_quality_ok": result.intelligence_quality_ok,
                    "ok": result.ok,
                    "error": result.error,
                    "benchmark_telemetry": result.benchmark_telemetry,
                }
                for result in results_by_mode[mode]
            ]
            for mode in ("off", "on")
        },
    }

    report["suite_gate_results"] = _suite_gate_results(suite_metrics_by_mode, args)
    failures = _check_gates(cases, report, args)
    report["gate_failures"] = failures
    report["overall_pass"] = not failures
    report["release_verdict"] = "ship" if not failures else "block"
    report["pilot_readiness_verdict"] = _pilot_verdict(report)

    _print_report(report)
    report_path = pathlib.Path(args.report_json).expanduser().resolve()
    _write_report_json(report_path, report)
    print(f"report_json={report_path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
