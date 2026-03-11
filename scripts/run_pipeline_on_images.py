#!/usr/bin/env python3
"""
Run full pipeline (vision → reasoning → verdict) on selected images.

Home events: push full metadata (camera label, zone, event context).
Non-home events: send image only (no camera/zone hints) to see how AI responds.

Usage: PYTHONPATH=. python scripts/run_pipeline_on_images.py [<image_path> ...]
       PYTHONPATH=. python scripts/run_pipeline_on_images.py --out pipeline_output.md

Default: runs 5 images (3 home from VIRAT, 2 non-home from Avenue).
Use --out <file.md> to write full structured output (VISION + REASONING) to a markdown file.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np

# Configure logging so LOG_LEVEL=DEBUG shows Phase1/agent timing
_log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.WARNING),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)


def _load_frame(path: str) -> tuple[np.ndarray, str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    data = p.read_bytes()
    arr = np.frombuffer(data, np.uint8)
    import cv2
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"Could not decode image: {path}")
    b64 = base64.b64encode(data).decode("utf-8")
    return frame, b64


def _is_home_event(path: str) -> bool:
    """Avenue dataset = non-home (subway/street). VIRAT and others = home."""
    name = Path(path).name.lower()
    return not name.startswith("avenue")


def _format_verdict_md(path: str, is_home: bool, out: dict) -> str:
    """Format verdict as markdown with clear VISION vs REASONING sections."""
    lines = [
        "",
        "## " + Path(path).name,
        "",
        f"**Mode:** {'HOME (full metadata)' if is_home else 'NON-HOME (image only)'}",
        "",
        "### VISION (from vision model – raw scene analysis)",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| description | {out.get('description', '')} |",
        f"| categories | {out.get('categories', [])} |",
    ]
    # Perception/observed evidence from vision
    case = out.get("case") or {}
    perception = case.get("perception") or {}
    obs = perception.get("observed_evidence") or []
    for e in obs:
        if e.get("source") == "vision":
            lines.append(f"| {e.get('kind', '')} | {e.get('claim', '')} |")
    lines.extend([
        "",
        "### REASONING (from reasoning agents – threat assessment)",
        "",
    ])
    for i, agent in enumerate(out.get("agent_outputs") or []):
        lines.extend([
            f"**{agent.get('agent_id', '')}** ({agent.get('role', '')})",
            f"- verdict: {agent.get('verdict', '')}",
            f"- rationale: {agent.get('rationale', '')}",
            "",
        ])
    lines.extend([
        "**Decision reason:**",
        f"```\n{out.get('decision_reason', '')}\n```",
        "",
        "### FINAL (summary & headlines – derived from vision + reasoning)",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| summary (headline) | {out.get('summary', '')} |",
        f"| consumer_summary.headline | {case.get('consumer_summary', {}).get('headline', '')} |",
        f"| consumer_summary.reason | {case.get('consumer_summary', {}).get('reason', '')} |",
        f"| action | {out.get('action', '')} |",
        f"| risk_level | {out.get('risk_level', '')} |",
        "",
        "<details><summary>Full raw JSON</summary>",
        "",
        "```json",
        json.dumps(out, indent=2, default=str),
        "```",
        "",
        "</details>",
        "",
        "---",
        "",
    ])
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run pipeline on images")
    parser.add_argument("--out", "-o", help="Write full output to markdown file")
    parser.add_argument("images", nargs="*", help="Image paths")
    args = parser.parse_args()

    if not args.images:
        # Default: 5 images (3 home, 2 non-home)
        default_images = [
            "datasets/pipeline_test_samples/1006_jpg.rf.e873527d4d76f54db2d0165df1449705.jpg",
            "datasets/pipeline_test_samples/17_jpg.rf.6fc3c4ff0a05642c38c7b89f5f55af78.jpg",
            "datasets/pipeline_test_samples/1219_jpg.rf.a3df5660900c07791c1749ece57979a5.jpg",
            "datasets/pipeline_test_samples/avenue_01.jpg",
            "datasets/pipeline_test_samples/avenue_15.jpg",
        ]
        root = Path(__file__).resolve().parents[1]
        paths = [str(root / p) for p in default_images]
    else:
        paths = args.images

    out_md = args.out

    from backend.agent.pipeline import process_frame
    from backend.database import AsyncSessionLocal, init_db
    from backend.hub import pipeline_manager
    from backend.models.schemas import EventContext, StreamMeta
    from backend.public import public_verdict

    await init_db()
    pipeline_manager.init(db_factory=AsyncSessionLocal)

    md_sections: list[str] = []
    if out_md:
        md_sections.append("# Pipeline full output (VISION + REASONING)")
        md_sections.append("")
        md_sections.append("Each image shows: **VISION** (raw scene analysis), **REASONING** (agent verdicts), **FINAL** (headlines/summary).")
        md_sections.append("")

    for path in paths:
        try:
            frame, b64 = _load_frame(path)
        except Exception as e:
            print(f"SKIP {path}: {e}")
            continue

        is_home = _is_home_event(path)
        if is_home:
            meta = StreamMeta(
                stream_id="pipeline_test",
                label="Driveway Cam",
                site_id="home",
                zone="driveway",
                uri="direct",
            )
            event_context = EventContext(
                source="script",
                cam_id="pipeline_test",
                home_id="home",
                zone="driveway",
                label="Driveway Cam",
                ingest_mode="script",
                metadata={"path": path, "event": {"label": "person", "type": "motion", "zone": "driveway"}},
            )
        else:
            meta = StreamMeta(
                stream_id="pipeline_test",
                label="Test Camera",
                site_id="home",
                zone="unknown",
                uri="direct",
            )
            event_context = EventContext(
                source="script",
                cam_id="pipeline_test",
                home_id="home",
                zone="unknown",
                ingest_mode="script",
                metadata={"path": path, "include_context": False},
            )

        print("\n" + "=" * 70)
        print(f"IMAGE: {path}")
        print(f"      {frame.shape[1]}x{frame.shape[0]}  |  {'HOME (full metadata)' if is_home else 'NON-HOME (image only)'}")
        print("=" * 70)

        async with AsyncSessionLocal() as db:
            verdict = await process_frame(
                frame=frame,
                stream_meta=meta,
                db=db,
                groq_client=pipeline_manager.groq_client,
                event_context=event_context,
            )

        out = public_verdict(verdict)
        if out_md:
            md_sections.append(_format_verdict_md(path, is_home, out))
        print(json.dumps(out, indent=2, default=str))
        print()

    if out_md and md_sections:
        Path(out_md).write_text("\n".join(md_sections), encoding="utf-8")
        print(f"Wrote full output to {out_md}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
