#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import collections
import os
import statistics
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("INGEST_ASYNC_DEFAULT", "false")
os.environ.setdefault("INGEST_API_KEY", "test-ingest-key")


@dataclass
class RealWorldCase:
    case_id: str
    image_path: str | None
    image_url: str | None
    cam_id: str
    home_id: str
    zone: str
    expected_action: str | None
    expected_categories: list[str]
    context: str


@dataclass
class AgentVote:
    agent_id: str
    role: str
    verdict: str
    confidence: float
    rationale: str


@dataclass
class CaseResult:
    case: RealWorldCase
    http_status: int
    action: str
    severity: str
    confidence: float
    categories: list[str]
    summary: str
    decision_reasoning: str
    agent_votes: list[AgentVote]
    ok: bool
    error: str


def mock_verdict(case: RealWorldCase, event_id: str) -> Any:
    from backend.models.schemas import (
        AgentOutput,
        AuditTrail,
        LiabilityDigest,
        MachineRouting,
        OperatorSummary,
        Verdict,
    )

    expected_action = case.expected_action or "suppress"
    severity = "high" if expected_action == "alert" else "none"
    confidence = 0.82 if expected_action == "alert" else 0.74
    outputs = [
        AgentOutput(
            agent_id="context_baseline_reasoner",
            role="Threat Escalation",
            verdict=expected_action,
            confidence=confidence,
            rationale=f"Mock assessment for {case.zone} context.",
            chain_notes={},
        ),
        AgentOutput(
            agent_id="behavioural_pattern",
            role="Behavioural Pattern",
            verdict=expected_action,
            confidence=confidence - 0.05,
            rationale=f"Mock behavior pattern for {case.case_id}.",
            chain_notes={},
        ),
        AgentOutput(
            agent_id="falsification_auditor",
            role="Context & Asset Risk",
            verdict=expected_action,
            confidence=confidence - 0.03,
            rationale=f"Mock risk weighting for zone {case.zone}.",
            chain_notes={},
        ),
        AgentOutput(
            agent_id="executive_triage_commander",
            role="Adversarial Challenger",
            verdict="suppress" if expected_action == "alert" else "alert",
            confidence=0.55,
            rationale="Mock challenger offers alternate explanation.",
            chain_notes={},
        ),
    ]
    return Verdict(
        frame_id=event_id,
        event_id=event_id,
        stream_id=case.cam_id,
        site_id=case.home_id,
        timestamp=datetime.utcnow(),
        routing=MachineRouting(
            is_threat=expected_action == "alert",
            action=expected_action,
            severity=severity,
            categories=case.expected_categories or ["motion"],
        ),
        summary=OperatorSummary(
            headline=f"Mock {expected_action} for {case.zone} at {confidence:.0%} confidence.",
            narrative=f"Mock narrative for {case.case_id}.",
        ),
        audit=AuditTrail(
            liability_digest=LiabilityDigest(
                decision_reasoning=f"Mock decision reasoning for {case.case_id}",
                confidence_score=confidence,
            ),
            agent_outputs=outputs,
        ),
        description=f"Mock description for {case.case_id}",
        bbox=[],
        b64_thumbnail="",
    )


def load_manifest(path: pathlib.Path) -> list[RealWorldCase]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("Manifest must be a JSON array")
    cases: list[RealWorldCase] = []
    for idx, item in enumerate(payload, start=1):
        case_id = str(item.get("case_id") or f"case_{idx}")
        image_path = item.get("image_path")
        image_url = item.get("image_url")
        if not image_path and not image_url:
            raise ValueError(f"{case_id}: provide image_path or image_url")
        expected_action = item.get("expected_action")
        if expected_action is not None and expected_action not in {"alert", "suppress"}:
            raise ValueError(f"{case_id}: expected_action must be alert or suppress")
        expected_categories = [str(v) for v in item.get("expected_categories", [])]
        cases.append(
            RealWorldCase(
                case_id=case_id,
                image_path=str(image_path) if image_path else None,
                image_url=str(image_url) if image_url else None,
                cam_id=str(item.get("cam_id", f"cam_{idx}")),
                home_id=str(item.get("home_id", "home")),
                zone=str(item.get("zone", "front_door")),
                expected_action=expected_action,
                expected_categories=expected_categories,
                context=str(item.get("context", "")),
            )
        )
    return cases


def encode_image(path: pathlib.Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def build_payload(case: RealWorldCase) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cam_id": case.cam_id,
        "home_id": case.home_id,
        "zone": case.zone,
    }
    if case.image_path:
        image_file = pathlib.Path(case.image_path)
        if not image_file.exists():
            raise FileNotFoundError(f"Image not found: {image_file}")
        payload["image_b64"] = encode_image(image_file)
    else:
        payload["image_url"] = case.image_url
    return payload


def _extract_confidence(body: dict[str, Any]) -> float | None:
    """Extract confidence from evidence_digest or case.evidence_digest (public_verdict format)."""
    for key in ("evidence_digest", "case"):
        val = body.get(key)
        if isinstance(val, dict):
            val = val.get("evidence_digest")
        if isinstance(val, list) and val and isinstance(val[0], dict):
            c = val[0].get("confidence")
            if c is not None:
                return float(c)
    return None


def parse_agent_votes(response_json: dict[str, Any]) -> list[AgentVote]:
    audit = response_json.get("audit") if isinstance(response_json.get("audit"), dict) else {}
    outputs = audit.get("agent_outputs") or response_json.get("agent_outputs") or []
    votes: list[AgentVote] = []
    for raw in outputs:
        votes.append(
            AgentVote(
                agent_id=str(raw.get("agent_id", "")),
                role=str(raw.get("role", "")),
                verdict=str(raw.get("verdict", "")),
                confidence=float(raw.get("confidence", 0.0) or 0.0),
                rationale=str(raw.get("rationale", "")),
            )
        )
    return votes


def case_result_from_response(case: RealWorldCase, status_code: int, body: dict[str, Any]) -> CaseResult:
    if "routing" not in body and body.get("status") == "queued":
        return CaseResult(
            case=case,
            http_status=status_code,
            action="",
            severity="",
            confidence=0.0,
            categories=[],
            summary="",
            decision_reasoning="",
            agent_votes=[],
            ok=False,
            error="Ingest is running in async mode and returned queued status. Set INGEST_ASYNC_DEFAULT=false for direct verdict evaluation.",
        )

    # Support both nested (routing/audit) and flat (public_verdict) response formats
    routing = body.get("routing") if isinstance(body.get("routing"), dict) else {}
    audit = body.get("audit") if isinstance(body.get("audit"), dict) else {}
    liability_digest = audit.get("liability_digest") if isinstance(audit.get("liability_digest"), dict) else {}

    action = str(routing.get("action") or body.get("action", ""))
    severity = str(routing.get("severity") or body.get("severity", ""))
    categories = [str(v) for v in (routing.get("categories") or body.get("categories", []))]
    confidence = float(
        liability_digest.get("confidence_score")
        or _extract_confidence(body)
        or 0.0
    )
    summary_val = body.get("summary")
    summary = str(summary_val.get("headline", "")) if isinstance(summary_val, dict) else str(summary_val or "")
    decision_reasoning = str(
        liability_digest.get("decision_reasoning")
        or body.get("decision_reason")
        or ""
    )
    votes = parse_agent_votes(body)
    expected_ok = True
    if case.expected_action:
        expected_ok = action == case.expected_action
    return CaseResult(
        case=case,
        http_status=status_code,
        action=action,
        severity=severity,
        confidence=confidence,
        categories=categories,
        summary=summary,
        decision_reasoning=decision_reasoning,
        agent_votes=votes,
        ok=status_code == 200 and expected_ok,
        error="",
    )


def post_case(base_url: str, api_key: str, timeout: int, case: RealWorldCase) -> CaseResult:
    try:
        payload = build_payload(case)
    except Exception as exc:
        return CaseResult(
            case=case,
            http_status=0,
            action="",
            severity="",
            confidence=0.0,
            categories=[],
            summary="",
            decision_reasoning="",
            agent_votes=[],
            ok=False,
            error=str(exc),
        )

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/novin/ingest",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return case_result_from_response(case, resp.status, body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return CaseResult(
            case=case,
            http_status=exc.code,
            action="",
            severity="",
            confidence=0.0,
            categories=[],
            summary="",
            decision_reasoning="",
            agent_votes=[],
            ok=False,
            error=f"HTTP {exc.code}: {detail[:300]}",
        )
    except Exception as exc:
        return CaseResult(
            case=case,
            http_status=0,
            action="",
            severity="",
            confidence=0.0,
            categories=[],
            summary="",
            decision_reasoning="",
            agent_votes=[],
            ok=False,
            error=str(exc),
        )


def post_case_inprocess(client: Any, api_key: str, case: RealWorldCase) -> CaseResult:
    try:
        payload = build_payload(case)
    except Exception as exc:
        return CaseResult(
            case=case,
            http_status=0,
            action="",
            severity="",
            confidence=0.0,
            categories=[],
            summary="",
            decision_reasoning="",
            agent_votes=[],
            ok=False,
            error=str(exc),
        )

    try:
        resp = client.post(
            "/api/novin/ingest",
            json=payload,
            headers={"x-api-key": api_key},
        )
        body = resp.json()
        if not isinstance(body, dict):
            return CaseResult(
                case=case,
                http_status=resp.status_code,
                action="",
                severity="",
                confidence=0.0,
                categories=[],
                summary="",
                decision_reasoning="",
                agent_votes=[],
                ok=False,
                error=f"Unexpected response body type: {type(body)}",
            )
        return case_result_from_response(case, resp.status_code, body)
    except Exception as exc:
        return CaseResult(
            case=case,
            http_status=0,
            action="",
            severity="",
            confidence=0.0,
            categories=[],
            summary="",
            decision_reasoning="",
            agent_votes=[],
            ok=False,
            error=str(exc),
        )


def print_case(result: CaseResult) -> None:
    expected = result.case.expected_action or "-"
    status = "PASS" if result.ok else "FAIL"
    print(f"[{status}] {result.case.case_id} | expected={expected} got={result.action or '-'} | http={result.http_status}")
    print(f"  zone={result.case.zone} severity={result.severity or '-'} confidence={result.confidence:.2f}")
    if result.case.context:
        print(f"  context={result.case.context}")
    if result.categories:
        print(f"  categories={', '.join(result.categories)}")
    if result.summary:
        print(f"  summary={result.summary[:220]}")
    if result.decision_reasoning:
        print(f"  decision={result.decision_reasoning[:220]}")
    if result.agent_votes:
        print("  agent_votes:")
        for vote in result.agent_votes:
            print(f"    - {vote.agent_id} ({vote.role}) => {vote.verdict} @ {vote.confidence:.2f}")
            if vote.rationale:
                print(f"      rationale={vote.rationale[:200]}")
    if result.error:
        print(f"  error={result.error}")
    print("")


def print_summary(results: list[CaseResult]) -> int:
    total = len(results)
    total_ok = sum(1 for r in results if r.ok)
    evaluated = [r for r in results if r.case.expected_action is not None]
    evaluated_ok = sum(1 for r in evaluated if r.ok)
    evaluated_accuracy = (evaluated_ok / len(evaluated)) if evaluated else 0.0
    confidences = [r.confidence for r in results if r.http_status == 200]
    avg_conf = statistics.mean(confidences) if confidences else 0.0
    alerts = sum(1 for r in results if r.action == "alert")
    suppresses = sum(1 for r in results if r.action == "suppress")

    print("=== Deployment Real-World Evaluation Summary ===")
    print(f"cases={total} passed={total_ok} failed={total-total_ok}")
    print(f"evaluated_cases={len(evaluated)} evaluated_accuracy={evaluated_accuracy:.1%}")
    print(f"action_distribution=alert:{alerts} suppress:{suppresses}")
    print(f"avg_confidence={avg_conf:.2f}")

    consensus_count = 0
    consensus_hits = 0
    for r in results:
        if not r.agent_votes or not r.action:
            continue
        votes = [v.verdict for v in r.agent_votes if v.verdict in {"alert", "suppress"}]
        if not votes:
            continue
        consensus_count += 1
        majority = collections.Counter(votes).most_common(1)[0][0]
        if majority == r.action:
            consensus_hits += 1
    if consensus_count:
        print(f"agent_majority_alignment={consensus_hits}/{consensus_count} ({(consensus_hits/consensus_count):.1%})")
    print("")

    return 0 if all(r.http_status == 200 for r in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deployment-focused real-world simulations and print agent response traces")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="test-ingest-key")
    parser.add_argument(
        "--manifest",
        default="/Users/Ollie/novin-home/test/fixtures/eval/deployment_realworld_manifest.json",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--inprocess", action="store_true")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()

    manifest_path = pathlib.Path(args.manifest)
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    cases = load_manifest(manifest_path)
    if args.max_cases > 0:
        cases = cases[: args.max_cases]
    if not cases:
        print("No cases found in manifest", file=sys.stderr)
        return 2

    print("=== Running Deployment Real-World Simulations ===")
    mode = "inprocess" if args.inprocess else f"http:{args.base_url}"
    print(f"mode={mode} cases={len(cases)}")
    print("")

    if args.inprocess:
        from fastapi.testclient import TestClient
        from backend.main import app

        if args.mock:
            async def _mock_process(frame, stream_meta, db, groq_client, event_id=None, event_context=None, **kwargs):
                if event_id is None:
                    event_id = f"mock-{stream_meta.stream_id}"
                matched = next((c for c in cases if c.cam_id == stream_meta.stream_id), None)
                if matched is None:
                    matched = cases[0]
                return mock_verdict(matched, event_id)

            with patch("backend.agent.pipeline.process_frame", AsyncMock(side_effect=_mock_process)):
                with TestClient(app) as client:
                    results = [post_case_inprocess(client, args.api_key, case) for case in cases]
        else:
            with TestClient(app) as client:
                results = [post_case_inprocess(client, args.api_key, case) for case in cases]
    else:
        results = [post_case(args.base_url, args.api_key, args.timeout, case) for case in cases]
    for result in results:
        print_case(result)
    return print_summary(results)


if __name__ == "__main__":
    raise SystemExit(main())
