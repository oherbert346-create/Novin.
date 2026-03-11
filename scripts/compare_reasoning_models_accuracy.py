#!/usr/bin/env python3
"""
Compare reasoning-model accuracy on real images and synthetic home-security scenarios.

Runs the configured model list on the same inputs and reports action accuracy, risk-level
accuracy, and per-cohort breakdown.

Usage:
  # Real images (requires test fixtures + Groq vision)
  VISION_PROVIDER=groq REASONING_PROVIDER=groq PYTHONPATH=. uv run python scripts/compare_reasoning_models_accuracy.py --real

  # Synthetic scenarios (reasoning only, no vision)
  REASONING_PROVIDER=groq PYTHONPATH=. uv run python scripts/compare_reasoning_models_accuracy.py --synthetic

  # Synthetic holdout scenarios
  REASONING_PROVIDER=groq PYTHONPATH=. uv run python scripts/compare_reasoning_models_accuracy.py --synthetic \
    --synthetic-catalog test/fixtures/eval/home_security/synthetic/synthetic_vision_holdout_catalog.json \
    --limit-synthetic 15

  # Both
  VISION_PROVIDER=groq REASONING_PROVIDER=groq PYTHONPATH=. uv run python scripts/compare_reasoning_models_accuracy.py --real --synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent


def _run_with_model(model: str, args: list[str]) -> dict:
    """Run a subprocess with GROQ_REASONING_MODEL set, return parsed JSON from stdout."""
    env = os.environ.copy()
    env["GROQ_REASONING_MODEL"] = model
    env["PYTHONPATH"] = str(ROOT)
    env["INGEST_ASYNC_DEFAULT"] = "false"
    env.setdefault("INGEST_API_KEY", "test-ingest-key")
    cmd = [sys.executable, "-c", _INNER_SCRIPT] + args
    result = subprocess.run(
        cmd,
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        print(f"Error running {model}: {result.stderr}", file=sys.stderr)
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


_INNER_SCRIPT = r"""
import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

# Add project root (when run via -c, __file__ is undefined; use cwd)
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

def run_real():
    from fastapi.testclient import TestClient
    from backend.main import app

    manifest_path = ROOT / "test/fixtures/eval/home_security/home_security_validation_manifest.json"
    if not manifest_path.exists():
        print(json.dumps({"error": "manifest not found"}), file=sys.stderr)
        return {}
    cases = json.loads(manifest_path.read_text())
    if isinstance(cases, dict) and "scenarios" in cases:
        return {"error": "manifest uses scenario catalog, use flat manifest"}
    results = []
    headers = {"x-api-key": "test-ingest-key", "Content-Type": "application/json", "x-novin-benchmark": "on"}
    with TestClient(app) as client:
        for case in cases:
            img_path = case.get("image_path")
            if not img_path:
                continue
            p = Path(img_path)
            if not p.is_absolute():
                p = (ROOT / p).resolve()
            if not p.exists():
                results.append({"case_id": case.get("case_id"), "error": "image not found"})
                continue
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            payload = {
                "cam_id": case.get("cam_id", "cam"),
                "home_id": case.get("home_id", "home"),
                "zone": case.get("zone", "front_door"),
                "image_b64": b64,
            }
            r = client.post("/api/novin/ingest", json=payload, headers=headers)
            action = r.json().get("action", "") if r.status_code == 200 else ""
            risk = r.json().get("risk_level", "") if r.status_code == 200 else ""
            results.append({
                "case_id": case.get("case_id"),
                "expected_action": case.get("expected_action"),
                "expected_risk": "high" if case.get("expected_action") == "alert" else "none",
                "action": action,
                "risk_level": risk,
                "cohort": case.get("cohort", "benign"),
            })
    correct_action = sum(1 for x in results if x.get("expected_action") == x.get("action"))
    correct_risk = sum(1 for x in results if x.get("expected_risk") == x.get("risk_level"))
    by_cohort = {}
    for r in results:
        c = r.get("cohort", "unknown")
        if c not in by_cohort:
            by_cohort[c] = {"total": 0, "action_ok": 0}
        by_cohort[c]["total"] += 1
        if r.get("expected_action") == r.get("action"):
            by_cohort[c]["action_ok"] += 1
    return {
        "mode": "real",
        "total": len(results),
        "action_accuracy": correct_action / len(results) if results else 0,
        "risk_accuracy": correct_risk / len(results) if results else 0,
        "by_cohort": by_cohort,
        "results": results,
    }


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p


def run_synthetic(catalog_arg: str, limit: int):
    from backend.agent.bus import AgentMessageBus
    from backend.agent.reasoning.arbiter import run_reasoning
    from backend.agent.reasoning.sandbox import scenario_to_frame_packet
    from backend.config import settings
    from groq import AsyncGroq

    if settings.reasoning_provider != "groq":
        return {"error": "REASONING_PROVIDER must be groq"}
    client = AsyncGroq(api_key=settings.groq_api_key)
    catalog_path = _resolve_path(catalog_arg)
    if not catalog_path.exists():
        return {"error": "synthetic catalog not found"}
    raw = json.loads(catalog_path.read_text())
    scenarios = raw.get("scenarios", [])
    # Convert to sandbox format
    sandbox_scenarios = []
    for s in scenarios:
        sim = s.get("simulated_vision", {})
        exp = s.get("expected_judgement", {})
        cats = sim.get("categories", ["person"])
        entity = "person" if "person" in cats else ("pet" if "pet" in cats else ("vehicle" if "vehicle" in cats else "motion"))
        sandbox_scenarios.append({
            "scenario_id": s.get("scenario_id"),
            "zone": s.get("zone", "front_door"),
            "time_iso": s.get("time_context", "2026-03-08T12:00:00"),
            "entity": entity,
            "props": sim.get("visible_objects", []),
            "pathing": sim.get("description", "")[:200],
            "history_brief": " ".join(ctx) if isinstance(ctx := (s.get("expected_reasoning_inputs") or {}).get("history_context"), list) else (ctx or ""),
            "risk_cues": sim.get("risk_cues", []),
            "cohort": s.get("cohort", "ambiguous"),
            "expected_action": exp.get("action", "suppress"),
        })
    bus = AgentMessageBus(["context_baseline_reasoner", "trajectory_intent_assessor", "falsification_auditor", "executive_triage_commander"])
    results = []

    async def _run():
        for scenario in sandbox_scenarios[:limit]:
            packet = scenario_to_frame_packet(scenario)
            verdict = await run_reasoning(packet=packet, b64_thumbnail="", bus=bus, client=client, db=None)
            results.append({
                "scenario_id": scenario["scenario_id"],
                "expected_action": scenario["expected_action"],
                "action": verdict.routing.action,
                "risk_level": verdict.routing.risk_level,
                "cohort": scenario["cohort"],
            })

    asyncio.run(_run())
    correct = sum(1 for r in results if r["expected_action"] == r["action"])
    by_cohort = {}
    for r in results:
        c = r.get("cohort", "unknown")
        if c not in by_cohort:
            by_cohort[c] = {"total": 0, "action_ok": 0}
        by_cohort[c]["total"] += 1
        if r["expected_action"] == r["action"]:
            by_cohort[c]["action_ok"] += 1
    return {
        "mode": "synthetic",
        "total": len(results),
        "action_accuracy": correct / len(results) if results else 0,
        "by_cohort": by_cohort,
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument(
        "--synthetic-catalog",
        default="test/fixtures/eval/home_security/synthetic/synthetic_vision_authoring_catalog.json",
    )
    parser.add_argument("--limit-synthetic", type=int, default=50)
    args = parser.parse_args(sys.argv[1:])
    out = {}
    if args.real:
        out["real"] = run_real()
    if args.synthetic:
        out["synthetic"] = run_synthetic(args.synthetic_catalog, args.limit_synthetic)
    print(json.dumps(out, indent=2))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare reasoning-model accuracy")
    parser.add_argument("--real", action="store_true", help="Run on real images (manifest)")
    parser.add_argument("--synthetic", action="store_true", help="Run on synthetic home-security scenarios")
    parser.add_argument("--limit-synthetic", type=int, default=50, help="Max synthetic scenarios (default 50)")
    parser.add_argument(
        "--synthetic-catalog",
        default="test/fixtures/eval/home_security/synthetic/synthetic_vision_authoring_catalog.json",
        help="Synthetic scenario catalog to evaluate",
    )
    parser.add_argument("--show-failures", action="store_true", help="Print which cases failed")
    args = parser.parse_args()

    if not args.real and not args.synthetic:
        parser.print_help()
        return 0

    models = [
        ("qwen/qwen3-32b", "Qwen 3 32B"),
        ("openai/gpt-oss-120b", "GPT-OSS 120B"),
        ("llama-3.1-8b-instant", "Llama 3.1 8B"),
    ]

    print("=" * 70)
    print("REASONING MODEL ACCURACY COMPARISON")
    print("=" * 70)

    for model_id, label in models:
        print(f"\n--- {label} ---")
        run_args = []
        if args.real:
            run_args.append("--real")
        if args.synthetic:
            run_args.append("--synthetic")
            run_args.extend(["--synthetic-catalog", args.synthetic_catalog, "--limit-synthetic", str(args.limit_synthetic)])
        data = _run_with_model(model_id, run_args)
        if not data:
            print(f"  Failed to run {label}")
            continue
        if "real" in data and data["real"]:
            r = data["real"]
            if "error" in r:
                print(f"  Real: {r['error']}")
            else:
                print(f"  Real images: action={r['action_accuracy']:.1%} risk={r['risk_accuracy']:.1%} (n={r['total']})")
                for cohort, m in r.get("by_cohort", {}).items():
                    acc = m["action_ok"] / m["total"] if m["total"] else 0
                    print(f"    {cohort}: {acc:.1%} ({m['action_ok']}/{m['total']})")
        if "synthetic" in data and data["synthetic"]:
            s = data["synthetic"]
            if "error" in s:
                print(f"  Synthetic: {s['error']}")
            else:
                print(f"  Synthetic: action={s['action_accuracy']:.1%} (n={s['total']})")
                for cohort, m in s.get("by_cohort", {}).items():
                    acc = m["action_ok"] / m["total"] if m["total"] else 0
                    print(f"    {cohort}: {acc:.1%} ({m['action_ok']}/{m['total']})")
                if args.show_failures and s.get("results"):
                    fails = [r for r in s["results"] if r.get("expected_action") != r.get("action")]
                    if fails:
                        print(f"  Failures ({len(fails)}):")
                        for f in fails:
                            print(f"    {f.get('scenario_id')}: expected={f.get('expected_action')} got={f.get('action')} (cohort={f.get('cohort')})")
        if args.show_failures and "real" in data and data["real"] and "error" not in data["real"]:
            r = data["real"]
            fails = [x for x in r.get("results", []) if x.get("expected_action") != x.get("action")]
            if fails:
                print(f"  Real failures ({len(fails)}):")
                for f in fails:
                    print(f"    {f.get('case_id')}: expected={f.get('expected_action')} got={f.get('action')} (cohort={f.get('cohort')})")

    print("\n" + "=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
