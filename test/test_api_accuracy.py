from __future__ import annotations

import argparse
import base64
import json
import pathlib
import statistics
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class EvalCase:
    image_path: str
    expected_action: str
    stream_id: str
    label: str
    site_id: str
    zone: str
    note: str = ""


@dataclass
class EvalResult:
    case: EvalCase
    status_code: int
    action: str
    severity: str
    confidence: float
    summary: str
    ok: bool
    error: str = ""


def _load_manifest(path: pathlib.Path) -> list[EvalCase]:
    raw = json.loads(path.read_text())
    cases: list[EvalCase] = []
    for idx, item in enumerate(raw, start=1):
        cases.append(
            EvalCase(
                image_path=item["image_path"],
                expected_action=item["expected_action"],
                stream_id=item.get("stream_id", f"eval_stream_{idx}"),
                label=item.get("label", f"eval-camera-{idx}"),
                site_id=item.get("site_id", "eval-site"),
                zone=item.get("zone", "eval-zone"),
                note=item.get("note", ""),
            )
        )
    return cases


def _encode_image(path: pathlib.Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("utf-8")


def _post_case(base_url: str, api_key: str, case: EvalCase) -> EvalResult:
    image_file = pathlib.Path(case.image_path)
    if not image_file.exists():
        return EvalResult(
            case=case,
            status_code=0,
            action="",
            severity="",
            confidence=0.0,
            summary="",
            ok=False,
            error=f"image not found: {image_file}",
        )

    payload = {
        "b64_frame": _encode_image(image_file),
        "stream_id": case.stream_id,
        "label": case.label,
        "site_id": case.site_id,
        "zone": case.zone,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/ingest/frame",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            action = body.get("action", "")
            confidence = float(body.get("final_confidence", 0.0) or 0.0)
            return EvalResult(
                case=case,
                status_code=resp.status,
                action=action,
                severity=str(body.get("severity", "")),
                confidence=confidence,
                summary=str(body.get("summary", "")),
                ok=resp.status == 200 and action == case.expected_action,
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return EvalResult(
            case=case,
            status_code=exc.code,
            action="",
            severity="",
            confidence=0.0,
            summary="",
            ok=False,
            error=f"HTTP {exc.code}: {detail[:200]}",
        )
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            case=case,
            status_code=0,
            action="",
            severity="",
            confidence=0.0,
            summary="",
            ok=False,
            error=str(exc),
        )


def _print_report(results: list[EvalResult]) -> int:
    total = len(results)
    passed = sum(1 for r in results if r.ok)
    failed = total - passed
    accuracy = (passed / total) if total else 0.0

    confidences = [r.confidence for r in results if r.status_code == 200]
    avg_conf = statistics.mean(confidences) if confidences else 0.0

    print("=== Main API Real-Image Evaluation ===")
    print(f"cases={total} passed={passed} failed={failed} accuracy={accuracy:.1%} avg_conf={avg_conf:.2f}")
    print()

    for idx, r in enumerate(results, start=1):
        status = "PASS" if r.ok else "FAIL"
        print(f"[{idx}] {status} | image={r.case.image_path}")
        print(f"     expected={r.case.expected_action} got={r.action or '-'} http={r.status_code}")
        if r.status_code == 200:
            print(f"     severity={r.severity} confidence={r.confidence:.2f}")
            if r.summary:
                print(f"     summary={r.summary[:180]}")
        if r.case.note:
            print(f"     note={r.case.note}")
        if r.error:
            print(f"     error={r.error}")

    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate /api/ingest/frame accuracy on real-image cases")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="test123")
    parser.add_argument(
        "--manifest",
        default="/Users/Ollie/novin/test/real_image_manifest.example.json",
        help="Path to JSON array of eval cases",
    )
    args = parser.parse_args()

    manifest_path = pathlib.Path(args.manifest)
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    cases = _load_manifest(manifest_path)
    if not cases:
        print("Manifest has no cases", file=sys.stderr)
        return 2

    results = [_post_case(args.base_url, args.api_key, case) for case in cases]
    return _print_report(results)


if __name__ == "__main__":
    raise SystemExit(main())
