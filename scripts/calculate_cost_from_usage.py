#!/usr/bin/env python3
"""
Calculate cost from token usage (vision + reasoning) and project at scale.
Uses Groq pricing and typical deployment/DB costs.

Usage:
  PYTHONPATH=. python scripts/calculate_cost_from_usage.py
  PYTHONPATH=. python scripts/calculate_cost_from_usage.py --model gpt-oss-120b
  PYTHONPATH=. python scripts/calculate_cost_from_usage.py --vision-in 1200 --reasoning-in 5000
"""

from __future__ import annotations

import argparse


# Groq pricing per million tokens (USD)
GROQ_VISION = {"input": 0.11, "output": 0.34}   # Llama 4 Scout 17B
GROQ_REASONING_8B = {"input": 0.05, "output": 0.08}   # Llama 3.1 8B
GROQ_REASONING_120B = {"input": 0.15, "output": 0.60}  # GPT-OSS 120B

# Default from telemetry (per frame, measured via benchmark_pipeline.py)
DEFAULT_VISION = {"input": 1384, "output": 140}
DEFAULT_REASONING_8B = {"input": 5520, "output": 566}
DEFAULT_REASONING_120B = {"input": 5740, "output": 797}


def cost_per_frame(
    vision_in: int,
    vision_out: int,
    reasoning_in: int,
    reasoning_out: int,
    reasoning_pricing: dict[str, float],
) -> tuple[float, float, float]:
    """Return (vision_cost, reasoning_cost, total_cost) per frame in USD."""
    v = (vision_in * GROQ_VISION["input"] + vision_out * GROQ_VISION["output"]) / 1e6
    r = (reasoning_in * reasoning_pricing["input"] + reasoning_out * reasoning_pricing["output"]) / 1e6
    return v, r, v + r


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=["llama-8b", "gpt-oss-120b"], default="gpt-oss-120b",
                   help="Reasoning model (default: gpt-oss-120b)")
    p.add_argument("--vision-in", type=int, default=None)
    p.add_argument("--vision-out", type=int, default=None)
    p.add_argument("--reasoning-in", type=int, default=None)
    p.add_argument("--reasoning-out", type=int, default=None)
    p.add_argument("--frames", type=int, default=0, help="Project cost for N frames")
    args = p.parse_args()

    if args.model == "llama-8b":
        reasoning_pricing = GROQ_REASONING_8B
        default_reasoning = DEFAULT_REASONING_8B
    else:
        reasoning_pricing = GROQ_REASONING_120B
        default_reasoning = DEFAULT_REASONING_120B

    vision_in = args.vision_in if args.vision_in is not None else DEFAULT_VISION["input"]
    vision_out = args.vision_out if args.vision_out is not None else DEFAULT_VISION["output"]
    reasoning_in = args.reasoning_in if args.reasoning_in is not None else default_reasoning["input"]
    reasoning_out = args.reasoning_out if args.reasoning_out is not None else default_reasoning["output"]

    v_cost, r_cost, total = cost_per_frame(
        vision_in, vision_out,
        reasoning_in, reasoning_out,
        reasoning_pricing,
    )

    print("=== Per-frame cost (Groq) ===")
    print(f"  Reasoning model: {args.model}")
    print(f"  Vision:    ${v_cost:.6f}")
    print(f"  Reasoning: ${r_cost:.6f}")
    print(f"  Total:    ${total:.6f} ({total*100:.4f}¢)")

    if args.frames:
        print(f"\n=== Projected for {args.frames:,} frames ===")
        print(f"  API cost: ${total * args.frames:,.2f}")

    # Scale tiers
    tiers = [
        (15_000, "Pilot (15k frames/mo)"),
        (150_000, "Growth (150k frames/mo)"),
        (1_500_000, "Scale (1.5M frames/mo)"),
        (15_000_000, "Mass (15M frames/mo)"),
    ]
    print("\n=== Scale projection (API only) ===")
    for frames, label in tiers:
        api = total * frames
        print(f"  {label}: ${api:,.2f}/mo")


if __name__ == "__main__":
    main()
