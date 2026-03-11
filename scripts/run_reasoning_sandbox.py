#!/usr/bin/env python3
"""
Run reasoning agents against synthetic scenarios (Text-to-Text Sandbox).

No video or vision API. Scenarios are converted to FramePackets with simulated vision telemetry.
Use this to validate reasoning logic before shadow-launching vision.

Usage:
  python scripts/run_reasoning_sandbox.py --scenarios test/fixtures/eval/reasoning_sandbox/synthetic_scenarios.jsonl
  python scripts/run_reasoning_sandbox.py --scenarios ... --limit 50  # quick smoke test
  python scripts/run_reasoning_sandbox.py --scenarios ... --output test/reports/reasoning_sandbox_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agent.bus import AgentMessageBus
from backend.agent.reasoning.arbiter import run_reasoning
from backend.agent.reasoning.sandbox import scenario_to_frame_packet
from backend.config import settings
from groq import AsyncGroq
from openai import AsyncOpenAI


_REASONING_AGENT_IDS = [
    "context_baseline_reasoner",
    "trajectory_intent_assessor",
    "falsification_auditor",
    "executive_triage_commander",
]


def _get_reasoning_client():
    """Return the client used by reasoning agents (provider-specific)."""
    if settings.reasoning_provider == "groq":
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY required for reasoning_provider=groq")
        return AsyncGroq(api_key=settings.groq_api_key)
    # Cerebras, SiliconFlow, Together use internal clients; pass None and agents use their own
    return None


def _verdict_to_explainable(verdict) -> dict:
    """Extract summaries and rationales from verdict for explainability."""
    out: dict = {}
    # Consumer summary (homeowner-facing)
    cs = verdict.consumer_summary
    if cs:
        out["consumer_summary"] = {
            "headline": getattr(cs, "headline", "") or "",
            "reason": getattr(cs, "reason", "") or "",
            "action_now": getattr(cs, "action_now", "") or "",
        }
    # Operator summary (central station)
    os_ = verdict.operator_summary
    if os_:
        out["operator_summary"] = {
            "what_observed": getattr(os_, "what_observed", "") or "",
            "why_flagged": getattr(os_, "why_flagged", "") or "",
            "why_not_benign": getattr(os_, "why_not_benign", "") or "",
            "what_is_uncertain": getattr(os_, "what_is_uncertain", "") or "",
            "timeline_context": getattr(os_, "timeline_context", "") or "",
            "recommended_next_step": getattr(os_, "recommended_next_step", "") or "",
        }
    # Evidence digest
    if verdict.evidence_digest:
        out["evidence_digest"] = [
            {"kind": getattr(e, "kind", ""), "claim": getattr(e, "claim", ""), "source": getattr(e, "source", ""), "status": getattr(e, "status", "")}
            for e in verdict.evidence_digest[:8]
        ]
    # Judgement (decision rationale)
    j = verdict.judgement
    if j:
        out["judgement"] = {
            "decision_rationale": getattr(j, "decision_rationale", "") or "",
            "contradiction_markers": getattr(j, "contradiction_markers", []) or [],
        }
    # Liability digest (decision reasoning)
    ld = verdict.audit.liability_digest
    if ld:
        out["decision_reasoning"] = getattr(ld, "decision_reasoning", "") or ""
    # Per-agent outputs (rationale, verdict, chain_notes)
    if verdict.audit.agent_outputs:
        out["agent_outputs"] = [
            {
                "agent_id": o.agent_id,
                "verdict": o.verdict,
                "risk_level": o.risk_level,
                "rationale": (o.rationale or "")[:500],
                "chain_notes": o.chain_notes or {},
            }
            for o in verdict.audit.agent_outputs
        ]
    return out


async def _run_one(
    scenario: dict,
    client,
    bus: AgentMessageBus,
) -> tuple[dict, dict]:
    """Run reasoning on one scenario. Returns (scenario_meta, result)."""
    packet = scenario_to_frame_packet(scenario)
    verdict = await run_reasoning(
        packet=packet,
        b64_thumbnail="",
        bus=bus,
        client=client,
        db=None,
    )
    result = {
        "action": verdict.routing.action,
        "risk_level": verdict.routing.risk_level,
        "confidence": verdict.audit.liability_digest.confidence_score,
        "reasoning_latency_ms": verdict.telemetry.get("reasoning_latency_ms"),
        "phase1_ms": verdict.telemetry.get("reasoning_phase1_latency_ms"),
        "phase2_ms": verdict.telemetry.get("reasoning_phase2_latency_ms"),
    }
    result["explainability"] = _verdict_to_explainable(verdict)
    return (
        {
            "scenario_id": scenario.get("scenario_id"),
            "cohort": scenario.get("cohort"),
            "expected_action": scenario.get("expected_action"),
        },
        result,
    )


def _load_scenarios(path: Path, limit: int | None) -> list[dict]:
    scenarios = []
    with open(path) as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                scenarios.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return scenarios


def _compute_metrics(results: list[tuple[dict, dict]]) -> dict:
    total = len(results)
    if not total:
        return {"total": 0}

    action_ok = sum(
        1 for meta, res in results
        if meta.get("expected_action") and res.get("action") == meta["expected_action"]
    )
    by_cohort: dict[str, list] = defaultdict(list)
    for meta, res in results:
        by_cohort[meta.get("cohort", "unknown")].append((meta, res))

    cohort_accuracy = {}
    for cohort, cohort_results in by_cohort.items():
        expected = [m.get("expected_action") for m, _ in cohort_results if m.get("expected_action")]
        actual = [r.get("action") for _, r in cohort_results]
        match = sum(1 for e, a in zip(expected, actual) if e == a)
        cohort_accuracy[cohort] = match / len(cohort_results) if cohort_results else 0.0

    latencies = [r.get("reasoning_latency_ms") for _, r in results if r.get("reasoning_latency_ms")]
    return {
        "total": total,
        "action_accuracy": action_ok / total,
        "action_correct": action_ok,
        "cohort_accuracy": cohort_accuracy,
        "p50_latency_ms": sorted(latencies)[len(latencies) // 2] if latencies else None,
        "p95_latency_ms": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 20 else (latencies[-1] if latencies else None),
        "mean_latency_ms": sum(latencies) / len(latencies) if latencies else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reasoning sandbox on synthetic scenarios")
    parser.add_argument("--scenarios", default="test/fixtures/eval/reasoning_sandbox/synthetic_scenarios.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    path = Path(args.scenarios).expanduser().resolve()
    if not path.exists():
        print(f"Scenarios file not found: {path}", file=sys.stderr)
        return 2

    scenarios = _load_scenarios(path, args.limit)
    if not scenarios:
        print("No scenarios loaded", file=sys.stderr)
        return 2

    try:
        client = _get_reasoning_client()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

    bus = AgentMessageBus(_REASONING_AGENT_IDS)
    results: list[tuple[dict, dict]] = []

    async def _run_all():
        for i, scenario in enumerate(scenarios):
            meta, res = await _run_one(scenario, client, bus)
            results.append((meta, res))
            if (i + 1) % 10 == 0:
                print(f"  processed {i + 1}/{len(scenarios)}...", file=sys.stderr)

    asyncio.run(_run_all())

    metrics = _compute_metrics(results)
    print(json.dumps(metrics, indent=2))

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "metrics": metrics,
            "results": [
                {
                    "scenario_id": m.get("scenario_id"),
                    "cohort": m.get("cohort"),
                    "expected": m.get("expected_action"),
                    "actual": r.get("action"),
                    "risk_level": r.get("risk_level"),
                    "confidence": r.get("confidence"),
                    "reasoning_latency_ms": r.get("reasoning_latency_ms"),
                    "explainability": r.get("explainability", {}),
                }
                for m, r in results
            ],
        }
        out_path.write_text(json.dumps(report, indent=2))
        print(f"report written to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
