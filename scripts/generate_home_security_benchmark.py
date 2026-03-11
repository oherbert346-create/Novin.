from __future__ import annotations

import argparse
import json
from pathlib import Path

from test.test_api_accuracy import _expand_scenario_catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a flat Novin Home benchmark manifest from event scenarios.")
    parser.add_argument(
        "--catalog",
        default="test/fixtures/eval/home_security/event_scenario_catalog.json",
        help="Path to the event scenario catalog JSON",
    )
    parser.add_argument(
        "--output",
        default="test/fixtures/eval/home_security/generated_event_manifest.json",
        help="Path to the generated flat event manifest JSON",
    )
    args = parser.parse_args()

    catalog_path = Path(args.catalog).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    raw = json.loads(catalog_path.read_text())
    generated = _expand_scenario_catalog(raw, catalog_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(generated, indent=2))
    print(f"catalog={catalog_path}")
    print(f"generated_cases={len(generated)}")
    print(f"output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
