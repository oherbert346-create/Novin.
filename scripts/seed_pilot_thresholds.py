#!/usr/bin/env python3
"""
Seed HomeThresholdConfig with conservative warm defaults for pilot homes.

Eliminates cold-start problem: adaptive thresholds normally require 50+ events
before activating. This script pre-seeds sensible baselines so the system is
immediately well-calibrated for each pilot site.

Usage:
    PYTHONPATH=. python scripts/seed_pilot_thresholds.py --site-ids home1 home2 home3
    PYTHONPATH=. python scripts/seed_pilot_thresholds.py --site-ids home1 --preset outdoor
    PYTHONPATH=. python scripts/seed_pilot_thresholds.py --list-presets
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

# Preset configurations by environment type.
# Higher thresholds = more conservative = fewer false positives at cost of some sensitivity.
PRESETS: dict[str, dict] = {
    "default": {
        "vote_confidence_threshold": 0.60,
        "strong_vote_threshold": 0.75,
        "min_alert_confidence": 0.40,
        "description": "Balanced — good starting point for most homes",
    },
    "outdoor": {
        "vote_confidence_threshold": 0.65,
        "strong_vote_threshold": 0.80,
        "min_alert_confidence": 0.45,
        "description": "Outdoor/driveway — higher threshold to reduce wildlife/shadow FPs",
    },
    "indoor": {
        "vote_confidence_threshold": 0.55,
        "strong_vote_threshold": 0.70,
        "min_alert_confidence": 0.35,
        "description": "Indoor — lower threshold, fewer spurious triggers",
    },
    "high_sensitivity": {
        "vote_confidence_threshold": 0.50,
        "strong_vote_threshold": 0.65,
        "min_alert_confidence": 0.30,
        "description": "High sensitivity — catches more events, higher FP rate",
    },
    "conservative": {
        "vote_confidence_threshold": 0.75,
        "strong_vote_threshold": 0.85,
        "min_alert_confidence": 0.55,
        "description": "Very conservative — minimal FPs, may miss borderline events",
    },
}


async def seed_thresholds(site_ids: list[str], preset: str, overwrite: bool) -> None:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from backend.database import init_db, AsyncSessionLocal
    from backend.models.db import HomeThresholdConfig
    from sqlalchemy import select

    preset_cfg = PRESETS[preset]
    print(f"\nPreset '{preset}': {preset_cfg['description']}")
    print(f"  vote_confidence_threshold : {preset_cfg['vote_confidence_threshold']}")
    print(f"  strong_vote_threshold     : {preset_cfg['strong_vote_threshold']}")
    print(f"  min_alert_confidence      : {preset_cfg['min_alert_confidence']}")
    print()

    await init_db()

    async with AsyncSessionLocal() as db:
        for site_id in site_ids:
            result = await db.execute(
                select(HomeThresholdConfig).where(HomeThresholdConfig.site_id == site_id)
            )
            existing = result.scalar_one_or_none()

            if existing and not overwrite:
                print(f"  [{site_id}] SKIP — already has thresholds (use --overwrite to replace)")
                continue

            now = datetime.utcnow()
            if existing:
                existing.vote_confidence_threshold = preset_cfg["vote_confidence_threshold"]
                existing.strong_vote_threshold = preset_cfg["strong_vote_threshold"]
                existing.min_alert_confidence = preset_cfg["min_alert_confidence"]
                existing.tuning_reason = f"pilot seed ({preset})"
                existing.last_tuned = now
                existing.updated_at = now
                print(f"  [{site_id}] UPDATED with preset '{preset}'")
            else:
                config = HomeThresholdConfig(
                    site_id=site_id,
                    vote_confidence_threshold=preset_cfg["vote_confidence_threshold"],
                    strong_vote_threshold=preset_cfg["strong_vote_threshold"],
                    min_alert_confidence=preset_cfg["min_alert_confidence"],
                    fp_count_30d=0,
                    fn_count_30d=0,
                    total_alerts_30d=0,
                    last_tuned=now,
                    tuning_reason=f"pilot seed ({preset})",
                )
                db.add(config)
                print(f"  [{site_id}] SEEDED with preset '{preset}'")

        await db.commit()

    print("\nDone. Adaptive feedback loop will adjust from these baselines as events accumulate.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed HomeThresholdConfig for pilot homes (eliminates cold start)"
    )
    parser.add_argument(
        "--site-ids",
        nargs="+",
        metavar="SITE_ID",
        help="One or more site IDs to seed (e.g. home1 home2)",
    )
    parser.add_argument(
        "--preset",
        default="default",
        choices=list(PRESETS.keys()),
        help="Threshold preset to apply (default: 'default')",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing threshold configs (default: skip existing)",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="List available presets and exit",
    )
    args = parser.parse_args()

    if args.list_presets:
        print("\nAvailable threshold presets:\n")
        for name, cfg in PRESETS.items():
            print(f"  {name:18s}  {cfg['description']}")
            print(f"    vote_confidence={cfg['vote_confidence_threshold']}  "
                  f"strong_vote={cfg['strong_vote_threshold']}  "
                  f"min_alert={cfg['min_alert_confidence']}")
        print()
        return

    if not args.site_ids:
        parser.error("--site-ids is required (or use --list-presets)")

    asyncio.run(seed_thresholds(args.site_ids, args.preset, args.overwrite))


if __name__ == "__main__":
    main()
