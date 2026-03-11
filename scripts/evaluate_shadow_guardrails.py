#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class EvalCase:
    case_id: str
    cam_id: str
    home_id: str
    zone: str
    image_url: str | None
    image_b64: str | None


def load_cases(path: pathlib.Path) -> list[EvalCase]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("Manifest must be a JSON array")
    cases = []
    for idx, item in enumerate(payload, start=1):
        cases.append(
            EvalCase(
                case_id=str(item.get("case_id", f"case_{idx}")),
                cam_id=str(item.get("cam_id", f"guardrail_cam_{idx}")),
                home_id=str(item.get("home_id", "home")),
                zone=str(item.get("zone", "front_door")),
                image_url=item.get("image_url"),
                image_b64=item.get("image_b64"),
            )
        )
    return cases


def post_case(base_url: str, api_key: str, case: EvalCase) -> tuple[int, dict[str, Any]]:
    payload = {
        "cam_id": case.cam_id,
        "home_id": case.home_id,
        "zone": case.zone,
    }
    if case.image_b64:
        payload["image_b64"] = case.image_b64
    elif case.image_url:
        payload["image_url"] = case.image_url
    else:
        raise ValueError(f"{case.case_id}: missing image source")

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/novin/ingest",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "x-novin-benchmark": "1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return exc.code, {"detail": detail[:500]}


def explanation_quality(body: dict[str, Any]) -> dict[str, bool]:
    reason = str(body.get("decision_reason", "") or "")
    lowered = reason.lower()
    return {
        "evidence": "evidence:" in lowered,
        "uncertainty": "uncertainty:" in lowered,
        "policy_basis": "alert_basis:" in lowered or "suppress_basis:" in lowered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate shadow rollout guardrails from live API responses")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--manifest", default="test/fixtures/eval/prod_api_accuracy_manifest.json")
    parser.add_argument("--report-json", default="test/reports/shadow_guardrail_report.json")
    args = parser.parse_args()

    cases = load_cases(pathlib.Path(args.manifest).expanduser().resolve())
    results: list[dict[str, Any]] = []
    contradiction_count = 0
    completeness_count = 0
    explanation_pass_count = 0

    for case in cases:
        status, body = post_case(args.base_url, args.api_key, case)
        fields_complete = all(
            key in body and body.get(key) not in (None, "", [])
            for key in ("action", "risk_level", "severity", "summary", "decision_reason", "agent_outputs")
        )
        quality = explanation_quality(body)
        contradiction = "warn:" in str(body.get("decision_reason", "")).lower()
        contradiction_count += int(contradiction)
        completeness_count += int(fields_complete)
        explanation_pass_count += int(all(quality.values()))
        results.append(
            {
                "case_id": case.case_id,
                "status_code": status,
                "fields_complete": fields_complete,
                "explanation_quality": quality,
                "contradiction": contradiction,
                "action": body.get("action"),
                "risk_level": body.get("risk_level"),
                "severity": body.get("severity"),
                "decision_reason": body.get("decision_reason", ""),
            }
        )

    total = len(results)
    report = {
        "total_cases": total,
        "evaluated_cases": total,
        "contradiction_rate": contradiction_count / total if total else 0.0,
        "explanation_quality_rate": explanation_pass_count / total if total else 0.0,
        "explainability_completeness_rate": completeness_count / total if total else 0.0,
        "results": results,
    }
    report_path = pathlib.Path(args.report_json).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"report_json={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
