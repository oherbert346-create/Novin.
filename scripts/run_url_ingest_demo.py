#!/usr/bin/env python3
"""
Demo: Ingest with REAL image URLs, show full agent outputs and summaries.
  --mock  Use mock agents (no Groq) so you see full agent_outputs + summary
  (default) Use real Groq — requires valid GROQ_API_KEY
Run: PYTHONPATH=. uv run python scripts/run_url_ingest_demo.py [--mock]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, patch

# Env for test mode
os.environ.setdefault("INGEST_ASYNC_DEFAULT", "false")
os.environ.setdefault("INGEST_API_KEY", "test-ingest-key")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./demo_novin.db")

# Suppress noisy logs when printing output
logging.getLogger("httpx").setLevel(logging.WARNING)
_log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.WARNING),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("backend").setLevel(getattr(logging, _log_level, logging.WARNING))

REAL_URL = "http://images.cocodataset.org/val2017/000000000139.jpg"


def _mock_verdict(frame, stream_meta, db, groq_client, event_id):
    """Return a realistic Verdict with full agent_outputs and summary."""
    from backend.models.schemas import (
        AgentOutput,
        AuditTrail,
        LiabilityDigest,
        MachineRouting,
        OperatorSummary,
        Verdict,
    )
    eid = event_id or "demo-event-id"
    agent_outputs = [
        AgentOutput(
            agent_id="context_baseline_reasoner",
            role="Threat Escalation",
            verdict="suppress",
            confidence=0.72,
            rationale="Person in frame appears to be a resident or delivery person. No forced entry or suspicious behaviour.",
            chain_notes={},
        ),
        AgentOutput(
            agent_id="trajectory_intent_assessor",
            role="Behavioural Pattern",
            verdict="suppress",
            confidence=0.68,
            rationale="Routine activity pattern: person near front door, likely package delivery or resident arrival.",
            chain_notes={},
        ),
        AgentOutput(
            agent_id="context_asset_risk",
            role="Context & Asset Risk",
            verdict="suppress",
            confidence=0.65,
            rationale="Front door zone; daytime context. Low asset risk. No intrusion indicators.",
            chain_notes={},
        ),
        AgentOutput(
            agent_id="executive_triage_commander",
            role="Adversarial Challenger",
            verdict="suppress",
            confidence=0.80,
            rationale="Benign explanation: delivery person or resident. No evidence of threat.",
            chain_notes={},
        ),
    ]
    return Verdict(
        frame_id=eid,
        event_id=eid,
        stream_id=stream_meta.stream_id,
        site_id=stream_meta.site_id,
        timestamp=datetime.utcnow(),
        routing=MachineRouting(
            is_threat=False,
            action="suppress",
            severity="none",
            categories=["person", "motion"],
        ),
        summary=OperatorSummary(
            headline="No home security concern in front_door; routine activity at 72% confidence.",
            narrative=(
                "• Person detected near front door.\n"
                "• Agent consensus: 0 alert, 4 suppress.\n"
                "• Action: suppress — confidence below threshold or benign."
            ),
        ),
        audit=AuditTrail(
            liability_digest=LiabilityDigest(
                decision_reasoning="Confidence 72% below threshold or benign (pet, delivery, resident). Benign explanation: delivery person or resident.",
                confidence_score=0.72,
            ),
            agent_outputs=agent_outputs,
        ),
        description="Person standing near front door, possible package delivery",
        bbox=[],
        b64_thumbnail="",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Use mock agents (no Groq) to show full outputs")
    args = parser.parse_args()

    import httpx
    from fastapi.testclient import TestClient

    from backend.main import app

    print("=" * 70)
    print("INGEST DEMO — real URL fetch, full agent outputs & summary")
    print("=" * 70)

    url = REAL_URL
    print(f"\n1. Fetching image from URL: {url}")
    with httpx.Client(timeout=15) as c:
        resp = c.get(url)
        print(f"   HTTP {resp.status_code}, bytes: {len(resp.content)}")
        if resp.status_code != 200:
            print(f"   ERROR: {resp.text[:200]}")
            return 1

    payload = {
        "cam_id": "demo_cam",
        "home_id": "home",
        "image_url": url,
        "zone": "front_door",
    }
    headers = {
        "x-api-key": "test-ingest-key",
        "Content-Type": "application/json",
        "x-novin-benchmark": "on",
    }

    if args.mock:
        print("\n   [Using mock agents — no Groq calls]")
        async def _mock_process_frame(frame, stream_meta, db, groq_client, event_id=None, event_context=None, **kwargs):
            return _mock_verdict(frame, stream_meta, db, groq_client, event_id)
        mock_pf = AsyncMock(side_effect=_mock_process_frame)
        ctx = patch("backend.agent.pipeline.process_frame", mock_pf)
    else:
        from contextlib import nullcontext
        ctx = nullcontext()

    with ctx:
        with TestClient(app) as client:
            resp = client.post("/api/novin/ingest", json=payload, headers=headers)

    print(f"\n2. Ingest response HTTP {resp.status_code}")
    data = resp.json()

    if "routing" not in data:
        print(json.dumps(data, indent=2)[:800])
        return 0 if resp.status_code == 200 else 1

    print("\n--- ROUTING (Tier 1) ---")
    print(f"  action: {data['routing']['action']}")
    print(f"  severity: {data['routing']['severity']}")
    print(f"  categories: {data['routing']['categories']}")

    print("\n--- SUMMARY (Tier 2) ---")
    print(f"  headline: {data['summary']['headline']}")
    print(f"  narrative:\n    " + data['summary']['narrative'].replace("\n", "\n    "))

    print("\n--- AUDIT: AGENT OUTPUTS (Tier 3) ---")
    for o in data.get("audit", {}).get("agent_outputs", []):
        print(f"  [{o['agent_id']}] {o['role']}")
        print(f"    verdict: {o['verdict']} | confidence: {o['confidence']}")
        print(f"    rationale: {o['rationale'][:120]}...")

    print("\n--- LIABILITY DIGEST ---")
    ld = data.get("audit", {}).get("liability_digest", {})
    print(f"  confidence_score: {ld.get('confidence_score')}")
    print(f"  decision_reasoning: {ld.get('decision_reasoning', '')[:150]}...")

    bt = data.get("benchmark_telemetry", {})
    if bt:
        print("\n--- TOKEN USAGE (benchmark_telemetry) ---")
        print(f"  vision: prompt={bt.get('vision_prompt_tokens', 0)} completion={bt.get('vision_completion_tokens', 0)} total={bt.get('vision_total_tokens', 0)}")
        print(f"  reasoning: prompt={bt.get('reasoning_prompt_tokens', 0)} completion={bt.get('reasoning_completion_tokens', 0)} total={bt.get('reasoning_total_tokens', 0)}")
        print(f"  pipeline_latency_ms: {bt.get('pipeline_latency_ms')}")

    print("\n" + "=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
