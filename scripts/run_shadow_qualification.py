#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any


def run_command(cmd: list[str], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return {
        "command": cmd,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "ok": proc.returncode == 0,
    }


def get_json(url: str) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return exc.code, {"detail": detail[:500]}


def load_json(path: str) -> dict[str, Any]:
    report_path = pathlib.Path(path).expanduser().resolve()
    if not report_path.exists():
        return {}
    return json.loads(report_path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run shadow rollout qualification and collect evidence")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--manifest", default="test/fixtures/eval/prod_api_accuracy_manifest.json")
    parser.add_argument("--accuracy-report", default="test/reports/shadow_accuracy_report.json")
    parser.add_argument("--guardrail-report", default="test/reports/shadow_guardrail_report.json")
    parser.add_argument("--skip-deploy", action="store_true")
    parser.add_argument("--report-json", default="test/reports/shadow_qualification_report.json")
    args = parser.parse_args()

    env = dict(os.environ)
    env.setdefault("VISION_PROVIDER", "siliconflow")
    env.setdefault("REASONING_PROVIDER", "cerebras")
    env.setdefault("INGEST_ASYNC_DEFAULT", "false")
    env.setdefault("SHADOW_MODE", "true")

    commands: dict[str, dict[str, Any]] = {}
    if not args.skip_deploy:
        commands["deploy"] = run_command(["./scripts/deploy.sh"], env=env)

    status_code, readiness = get_json(f"{args.base_url.rstrip('/')}/health/ready")
    commands["readiness"] = {
        "command": ["GET", f"{args.base_url.rstrip('/')}/health/ready"],
        "exit_code": 0 if status_code == 200 else 1,
        "ok": status_code == 200,
        "status_code": status_code,
        "response": readiness,
    }

    commands["smoke"] = run_command(
        ["python3", "scripts/release_smoke_test.py"],
        env=env,
    )
    commands["deployment_realworld"] = run_command(
        [
            "python3",
            "scripts/run_deployment_realworld_tests.py",
            "--base-url",
            args.base_url,
        ],
        env=env,
    )
    commands["accuracy"] = run_command(
        [
            "python3",
            "test/test_api_accuracy.py",
            "--base-url",
            args.base_url,
            "--api-key",
            args.api_key,
            "--manifest",
            args.manifest,
            "--report-json",
            args.accuracy_report,
        ],
        env=env,
    )
    commands["guardrails"] = run_command(
        [
            "python3",
            "scripts/evaluate_shadow_guardrails.py",
            "--base-url",
            args.base_url,
            "--api-key",
            args.api_key,
            "--manifest",
            args.manifest,
            "--report-json",
            args.guardrail_report,
        ],
        env=env,
    )

    accuracy_report = load_json(args.accuracy_report)
    guardrail_report = load_json(args.guardrail_report)

    shadow_ready = (
        all(item.get("ok") for item in commands.values())
        and readiness.get("status") == "ok"
        and guardrail_report.get("contradiction_rate", 1.0) <= 0.05
    )
    pilot_ready = (
        accuracy_report.get("pilot_readiness_verdict") == "pilot-ready based on staged real benchmark"
        and guardrail_report.get("contradiction_rate", 1.0) <= 0.03
        and guardrail_report.get("explanation_quality_rate", 0.0) >= 0.90
        and guardrail_report.get("explainability_completeness_rate", 0.0) >= 1.0
    )

    report = {
        "target_stack": {
            "vision_provider": env["VISION_PROVIDER"],
            "reasoning_provider": env["REASONING_PROVIDER"],
            "ingest_async_default": env["INGEST_ASYNC_DEFAULT"],
            "shadow_mode": env["SHADOW_MODE"],
        },
        "commands": commands,
        "accuracy_report": accuracy_report,
        "guardrail_report": guardrail_report,
        "shadow_rollout_verdict": "ready" if shadow_ready else "blocked",
        "small_pilot_verdict": "ready" if pilot_ready else "blocked",
    }

    report_path = pathlib.Path(args.report_json).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"report_json={report_path}")
    return 0 if shadow_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
