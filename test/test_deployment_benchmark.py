from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from test.test_api_accuracy import (
    PRIMARY_GATE_SUITE,
    EvalCase,
    EvalResult,
    _build_incident_metrics,
    _build_metrics,
    _build_suite_metrics,
    _check_gates,
    _compare_memory,
    _expand_scenario_catalog,
    _pilot_verdict,
)


def _case(case_id: str, cohort: str, expected_action: str = "suppress") -> EvalCase:
    return EvalCase(
        scenario_id=f"scenario-{case_id}",
        scenario_family="unit_test",
        source_mode="nvr_webhook",
        suite=PRIMARY_GATE_SUITE,
        case_id=case_id,
        image_path="/tmp/fixture.jpg",
        image_url=None,
        expected_action=expected_action,
        expected_risk_level="high" if expected_action == "alert" else "none",
        expected_visibility_policy="prominent" if expected_action == "alert" else "hidden",
        expected_notification_policy="immediate" if expected_action == "alert" else "none",
        cam_id=f"cam-{case_id}",
        home_id="home",
        zone="front_door",
        cohort=cohort,
    )


def _result(case: EvalCase, repeat: int, action: str, *, degraded: bool = False, latency_s: float = 1.0) -> EvalResult:
    return EvalResult(
        case=case,
        repeat=repeat,
        memory_mode="off",
        status_code=200,
        action=action,
        risk_level="high" if action == "alert" else "none",
        visibility_policy="prominent" if action == "alert" else "hidden",
        notification_policy="immediate" if action == "alert" else "none",
        ok=action == case.expected_action and not degraded,
        latency_s=latency_s,
        reasoning_degraded=degraded,
        fallback_agent_count=1 if degraded else 0,
        context_ok=True,
        idempotency_ok=True,
        memory_present=False,
    )


def test_build_metrics_includes_reliability_surfaces():
    cases = [
        _case("b1", "benign"),
        _case("t1", "threat", expected_action="alert"),
        _case("a1", "ambiguous"),
    ]
    results = [
        _result(cases[0], 1, "suppress", latency_s=1.2),
        _result(cases[1], 1, "alert", latency_s=2.0),
        _result(cases[2], 1, "suppress", degraded=True, latency_s=1.5),
    ]

    metrics = _build_metrics(cases, results, repeats=1)

    assert "reasoning_degraded_rate" in metrics
    assert "fallback_agent_rate" in metrics
    assert "p95_latency_s" in metrics
    assert metrics["reasoning_degraded_rate"] > 0
    assert metrics["fallback_agent_rate"] > 0
    assert metrics["p95_latency_s"] >= metrics["p50_latency_s"]


def test_check_gates_fails_on_degraded_and_memory_regression():
    cases = [
        _case("b1", "benign"),
        _case("b2", "benign"),
        _case("b3", "benign"),
        _case("t1", "threat", expected_action="alert"),
        _case("t2", "threat", expected_action="alert"),
        _case("a1", "ambiguous"),
        _case("a2", "ambiguous"),
        _case("tmp1", "temporal"),
        _case("ctx1", "context"),
        _case("ctx2", "context"),
        _case("ops1", "ops"),
        _case("ops2", "ops"),
    ]
    report = {
        "metrics": {
            "off": {
                "accuracy_action": 0.9,
                "risk_level_accuracy": 0.9,
                "alert_recall": 0.8,
                "medium_or_higher_recall": 0.8,
                "none_rate_on_true_threats": 0.0,
                "false_alert_rate_benign": 0.0,
                "missed_alert_rate_threat": 0.2,
                "show_policy_accuracy": 1.0,
                "notification_policy_accuracy": 1.0,
                "queued_response_rate": 0.0,
                "entry_zone_threat_recall": 1.0,
                "tamper_recall": 1.0,
                "reasoning_degraded_rate": 0.0,
                "fallback_agent_rate": 0.0,
                "http_error_rate": 0.0,
                "context_propagation_rate": 1.0,
                "idempotency_correctness_rate": 1.0,
                "flip_rate": 0.0,
                "p95_latency_s": 2.0,
            },
            "on": {
                "accuracy_action": 0.8,
                "risk_level_accuracy": 0.6,
                "alert_recall": 0.8,
                "medium_or_higher_recall": 0.4,
                "none_rate_on_true_threats": 0.3,
                "false_alert_rate_benign": 0.0,
                "missed_alert_rate_threat": 0.2,
                "show_policy_accuracy": 0.7,
                "notification_policy_accuracy": 0.7,
                "queued_response_rate": 0.0,
                "entry_zone_threat_recall": 0.5,
                "tamper_recall": 0.5,
                "reasoning_degraded_rate": 0.2,
                "fallback_agent_rate": 0.1,
                "http_error_rate": 0.0,
                "context_propagation_rate": 1.0,
                "idempotency_correctness_rate": 1.0,
                "flip_rate": 0.0,
                "p95_latency_s": 2.0,
            },
        },
        "suite_metrics": {
            "off": {
                PRIMARY_GATE_SUITE: {
                    "gating_cases": {
                        "suite_case_ids": [case.case_id for case in cases],
                        "accuracy_action": 0.9,
                        "risk_level_accuracy": 0.9,
                        "alert_recall": 0.8,
                        "medium_or_higher_recall": 0.8,
                        "none_rate_on_true_threats": 0.0,
                        "false_alert_rate_benign": 0.0,
                        "missed_alert_rate_threat": 0.2,
                        "show_policy_accuracy": 1.0,
                        "notification_policy_accuracy": 1.0,
                        "queued_response_rate": 0.0,
                        "entry_zone_threat_recall": 1.0,
                        "tamper_recall": 1.0,
                        "reasoning_degraded_rate": 0.0,
                        "fallback_agent_rate": 0.0,
                        "http_error_rate": 0.0,
                        "context_propagation_rate": 1.0,
                        "idempotency_correctness_rate": 1.0,
                        "flip_rate": 0.0,
                        "p95_latency_s": 2.0,
                    }
                }
            },
            "on": {
                PRIMARY_GATE_SUITE: {
                    "gating_cases": {
                        "suite_case_ids": [case.case_id for case in cases],
                        "accuracy_action": 0.8,
                        "risk_level_accuracy": 0.6,
                        "alert_recall": 0.8,
                        "medium_or_higher_recall": 0.4,
                        "none_rate_on_true_threats": 0.3,
                        "false_alert_rate_benign": 0.0,
                        "missed_alert_rate_threat": 0.2,
                        "show_policy_accuracy": 0.7,
                        "notification_policy_accuracy": 0.7,
                        "queued_response_rate": 0.0,
                        "entry_zone_threat_recall": 0.5,
                        "tamper_recall": 0.5,
                        "reasoning_degraded_rate": 0.2,
                        "fallback_agent_rate": 0.1,
                        "http_error_rate": 0.0,
                        "context_propagation_rate": 1.0,
                        "idempotency_correctness_rate": 1.0,
                        "flip_rate": 0.0,
                        "p95_latency_s": 2.0,
                    }
                }
            },
        },
        "memory_comparison": {
            "delta": {
                "accuracy_action": -0.1,
                "false_alert_rate_benign": 0.0,
                "missed_alert_rate_threat": 0.0,
                "reasoning_degraded_rate": 0.2,
                "flip_rate": 0.0,
            }
        },
    }
    args = SimpleNamespace(
        min_total_cases=12,
        min_benign_cases=3,
        min_threat_cases=2,
        min_ambiguous_cases=2,
        min_temporal_cases=1,
        min_context_cases=2,
        min_ops_cases=2,
        min_action_accuracy=0.7,
        min_alert_recall=0.5,
        min_risk_level_accuracy=0.7,
        min_medium_or_higher_recall=0.7,
        max_none_rate_on_true_threats=0.1,
        min_show_policy_accuracy=0.8,
        min_notification_policy_accuracy=0.8,
        min_entry_zone_threat_recall=0.7,
        min_tamper_recall=0.7,
        max_queued_response_rate=0.0,
        max_false_alert_rate_benign=0.35,
        max_missed_alert_rate_threat=0.5,
        max_reasoning_degraded_rate=0.1,
        max_fallback_agent_rate=0.05,
        max_http_error_rate=0.0,
        min_context_propagation_rate=0.95,
        min_idempotency_correctness_rate=1.0,
        max_flip_rate=0.2,
        max_p95_latency_s=4.0,
        max_memory_accuracy_regression=0.0,
        max_memory_false_alert_regression=0.0,
        max_memory_missed_alert_regression=0.0,
        max_memory_reasoning_degraded_regression=0.0,
        max_memory_flip_regression=0.0,
    )

    failures = _check_gates(cases, report, args)

    assert failures
    assert any("min_risk_level_accuracy" in failure for failure in failures)
    assert any("max_reasoning_degraded_rate" in failure for failure in failures)
    assert any("memory_on regressed action accuracy" == failure for failure in failures)


def test_compare_memory_reports_deltas():
    comparison = _compare_memory(
        {
            "accuracy_action": 0.8,
            "false_alert_rate_benign": 0.1,
            "missed_alert_rate_threat": 0.2,
            "reasoning_degraded_rate": 0.05,
            "flip_rate": 0.1,
        },
        {
            "accuracy_action": 0.9,
            "false_alert_rate_benign": 0.05,
            "missed_alert_rate_threat": 0.1,
            "reasoning_degraded_rate": 0.0,
            "flip_rate": 0.0,
        },
    )

    assert comparison["delta"]["accuracy_action"] > 0
    assert comparison["delta"]["false_alert_rate_benign"] < 0


def test_build_metrics_excludes_idempotency_case_from_action_and_context_rates():
    action_case = _case("ctx-action", "context")
    idempotency_case = EvalCase(
        scenario_id="scenario-ctx-dup",
        scenario_family="unit_test",
        source_mode="nvr_webhook",
        suite=PRIMARY_GATE_SUITE,
        case_id="ctx-dup",
        image_path="/tmp/fixture.jpg",
        image_url=None,
        expected_action="suppress",
        expected_risk_level="none",
        expected_visibility_policy="hidden",
        expected_notification_policy="none",
        cam_id="cam-dup",
        home_id="home",
        zone="front_door",
        cohort="context",
        benchmark_kind="idempotency",
        expect_duplicate_status=True,
    )
    results = [
        _result(action_case, 1, "suppress"),
        EvalResult(
            case=idempotency_case,
            repeat=1,
            memory_mode="off",
            status_code=200,
            action="",
            risk_level="none",
            visibility_policy="hidden",
            notification_policy="none",
            ok=True,
            latency_s=1.0,
            reasoning_degraded=False,
            fallback_agent_count=0,
            context_ok=False,
            idempotency_ok=True,
            memory_present=False,
            response_status="duplicate",
        ),
    ]

    metrics = _build_metrics([action_case, idempotency_case], results, repeats=1)

    assert metrics["accuracy_action"] == 1.0
    assert metrics["context_propagation_rate"] == 0.0
    assert metrics["idempotency_correctness_rate"] == 1.0


def test_build_suite_metrics_includes_all_validation_suites():
    cases = [_case("b1", "benign")]
    results = [_result(cases[0], 1, "suppress")]

    suite_metrics = _build_suite_metrics(cases, results, repeats=1)

    assert PRIMARY_GATE_SUITE in suite_metrics
    assert "synthetic_home" in suite_metrics
    assert suite_metrics["synthetic_home"]["gating_cases"]["attempts_total"] == 0


def test_pilot_verdict_requires_staged_real_cases():
    report = {
        "suite_gate_results": {
            PRIMARY_GATE_SUITE: {
                "off": {"gating_case_count": 0, "pass": False},
                "on": {"gating_case_count": 0, "pass": False},
            }
        }
    }

    verdict = _pilot_verdict(report)

    assert verdict == "not pilot-ready; staged real benchmark dataset missing"


def test_expand_scenario_catalog_generates_event_cases():
    catalog = {
        "variant_axes": {
            "time_of_day": ["day", "night"],
            "weather": ["clear", "rain"],
        },
        "scenarios": [
            {
                "scenario_id": "delivery_flow",
                "suite": "synthetic_home",
                "scenario_family": "delivery",
                "source_mode": "nvr_webhook",
                "home_id": "home-a",
                "cameras": [
                    {"camera_id": "driveway_cam", "zone": "driveway"},
                    {"camera_id": "porch_cam", "zone": "porch"},
                ],
                "assets": {
                    "driveway": {"image_path": "/tmp/fixture.jpg"},
                    "porch": {"image_path": "/tmp/fixture.jpg"},
                },
                "timeline": [
                    {"event_id": "driveway", "camera_ref": "driveway_cam", "asset_ref": "driveway", "expected_action": "suppress"},
                    {"event_id": "porch", "camera_ref": "porch_cam", "asset_ref": "porch", "expected_action": "suppress"},
                ],
                "correlation_expectations": {
                    "incident_id_expected": "incident-delivery",
                    "cross_cam_correlation_expected": True,
                    "escalation_expected": ["suppress", "suppress"],
                    "final_incident_action_expected": "suppress",
                },
                "memory_expectations": {"expected_to_help": False},
            }
        ],
    }

    expanded = _expand_scenario_catalog(catalog, Path("/tmp/catalog.json"))

    assert len(expanded) == 8
    assert expanded[0]["scenario_id"] == "delivery_flow"
    assert expanded[0]["source_mode"] == "nvr_webhook"
    assert expanded[0]["final_incident_action_expected"] == "suppress"


def test_build_incident_metrics_tracks_cross_cam_and_escalation():
    case_one = _case("cam1", "temporal", expected_action="suppress")
    case_one.scenario_id = "incident-1"
    case_one.escalation_expected = ["suppress", "alert"]
    case_one.cross_cam_correlation_expected = True
    case_one.final_incident_action_expected = "alert"

    case_two = _case("cam2", "temporal", expected_action="alert")
    case_two.scenario_id = "incident-1"
    case_two.cam_id = "cam-2"
    case_two.escalation_expected = ["suppress", "alert"]
    case_two.cross_cam_correlation_expected = True
    case_two.final_incident_action_expected = "alert"

    incident = _build_incident_metrics(
        [
            _result(case_one, 1, "suppress"),
            _result(case_two, 1, "alert"),
        ]
    )

    assert incident["incident_final_action_accuracy"] == 1.0
    assert incident["cross_cam_correlation_rate"] == 1.0
    assert incident["incident_escalation_accuracy"] == 1.0
