#!/usr/bin/env python3
"""
Download surveillance datasets for Novin vision validation:
- RoboFlow home surveillance (Security System Annotation)
- Avenue (CUHK abnormal event detection)
- VIRAT (requires manual registration; script documents the process)

Usage:
  PYTHONPATH=. python scripts/download_surveillance_datasets.py [--avenue] [--roboflow] [--virat-info]
  Or set ROBOFLOW_API_KEY in .env and run with --roboflow to download.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"
AVENUE_URL = "https://www.cse.cuhk.edu.hk/leojia/projects/detectabnormal/Avenue_Dataset.zip"
AVENUE_GT_URL = "https://www.cse.cuhk.edu.hk/leojia/projects/detectabnormal/ground_truth_demo.zip"
ROBOFLOW_WORKSPACE = "home-automation-and-intruder-detection"
ROBOFLOW_PROJECT = "security-system-annotation"
VIRAT_AGREEMENT_URL = "https://viratdata.org/resources/VIRAT-Video-Data-Set-Protection-Agreement-1-4-11.pdf"
VIRAT_GROUND_URL = "https://data.kitware.com/#collection/56f56db28d777f753209ba9f/folder/56f57e748d777f753209bed6"
VIRAT_AERIAL_URL = "https://data.kitware.com/#collection/611e77a42fa25629b9daceba"


def download_avenue() -> bool:
    """Download Avenue dataset and ground truth from CUHK."""
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    avenue_zip = DATASETS_DIR / "Avenue_Dataset.zip"
    gt_zip = DATASETS_DIR / "Avenue_ground_truth.zip"

    if avenue_zip.exists() and avenue_zip.stat().st_size > 100_000_000:
        print(f"Avenue dataset already present: {avenue_zip} ({avenue_zip.stat().st_size / 1e6:.1f} MB)")
    else:
        print("Downloading Avenue dataset (may take several minutes)...")
        try:
            subprocess.run(
                ["curl", "-sL", "-o", str(avenue_zip), AVENUE_URL],
                check=True,
                timeout=600,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            print(f"Avenue download failed: {e}")
            print("Alternative: download from Kaggle: https://www.kaggle.com/datasets/janeshvarsivakumar/avenue-dataset")
            return False

    if not gt_zip.exists():
        print("Downloading Avenue ground truth...")
        subprocess.run(["curl", "-sL", "-o", str(gt_zip), AVENUE_GT_URL], check=True, timeout=60)

    print(f"Avenue: {avenue_zip} ({avenue_zip.stat().st_size / 1e6:.1f} MB)")
    print(f"Ground truth: {gt_zip}")
    return True


def download_roboflow() -> bool:
    """Download RoboFlow home surveillance dataset. Requires ROBOFLOW_API_KEY."""
    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        print("ROBOFLOW_API_KEY not set. Add to .env and retry.")
        print("Get key: https://app.roboflow.com/settings/api")
        return False

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = DATASETS_DIR / "roboflow_security_system"
    out_dir.mkdir(exist_ok=True)

    try:
        from roboflow import Roboflow

        rf = Roboflow(api_key=api_key)
        workspace = rf.workspace()
        # After forking from Universe, project name in your workspace
        project = workspace.project(ROBOFLOW_PROJECT)
        version = project.version(1)
        version.download("coco", location=str(out_dir))
        print(f"RoboFlow dataset saved to {out_dir}")
        return True
    except Exception as e:
        print(f"RoboFlow download failed: {e}")
        print()
        print("To download: 1) Sign up at roboflow.com")
        print("2) Fork: https://universe.roboflow.com/home-automation-and-intruder-detection/security-system-annotation")
        print("3) In your workspace, create a version and Download Dataset (or use same project name)")
        print("4) Or use: smartsurveillance/anomaly for 8k anomaly images")
        return False


def download_virat_annotations() -> bool:
    """Clone VIRAT annotations (public, no agreement required)."""
    out_dir = DATASETS_DIR / "virat_annotations"
    if (out_dir / ".git").exists():
        print(f"VIRAT annotations already cloned: {out_dir}")
        return True
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    print("Cloning VIRAT annotations (public)...")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "https://gitlab.kitware.com/viratdata/viratannotations.git", str(out_dir)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        print(f"VIRAT annotations: {out_dir}")
        return True
    except Exception as e:
        print(f"VIRAT annotations clone failed: {e}")
        return False


def print_virat_info() -> None:
    """Print VIRAT dataset download instructions (videos require manual agreement)."""
    print("=" * 60)
    print("VIRAT Video Dataset")
    print("=" * 60)
    print("VIRAT videos require signing the VIRAT Video Dataset Protection Agreement.")
    print()
    print("1. Read agreement:", VIRAT_AGREEMENT_URL)
    print("2. Ground camera data:", VIRAT_GROUND_URL)
    print("3. Aerial data:", VIRAT_AERIAL_URL)
    print()
    print("After approval, download from Kitware Data (data.kitware.com).")
    print("Annotations (public): https://gitlab.kitware.com/viratdata/viratannotations")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download surveillance datasets")
    parser.add_argument("--avenue", action="store_true", help="Download Avenue dataset")
    parser.add_argument("--roboflow", action="store_true", help="Download RoboFlow home surveillance")
    parser.add_argument("--virat", action="store_true", help="Clone VIRAT annotations (public)")
    parser.add_argument("--virat-info", action="store_true", help="Print VIRAT video download instructions")
    parser.add_argument("--all", action="store_true", help="Download all (Avenue + RoboFlow + VIRAT annotations)")
    args = parser.parse_args()

    if not any([args.avenue, args.roboflow, args.virat, args.virat_info, args.all]):
        parser.print_help()
        return 0

    # Load .env if present
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k == "ROBOFLOW_API_KEY" and v and not v.startswith("..."):
                    os.environ.setdefault("ROBOFLOW_API_KEY", v)

    ok = True
    if args.avenue or args.all:
        ok = download_avenue() and ok
    if args.roboflow or args.all:
        ok = download_roboflow() and ok  # needs ROBOFLOW_API_KEY
    if args.virat or args.all:
        ok = download_virat_annotations() and ok
    if args.virat_info or args.all:
        print_virat_info()

    # Only fail if explicitly requested download failed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
