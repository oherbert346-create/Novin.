#!/usr/bin/env python3
"""
Generate synthetic home-security scenarios for reasoning-agent testing (Text-to-Text Sandbox).

Uses an LLM to produce 1,000+ diverse JSON payloads describing tricky scenarios.
No video required—vision telemetry is simulated from the text.
Run reasoning agents against these to validate logic before shadow-launching vision.

Usage:
  python scripts/generate_synthetic_scenarios.py --count 1000 --output test/fixtures/eval/reasoning_sandbox/synthetic_scenarios.jsonl
  python scripts/generate_synthetic_scenarios.py --count 100 --concurrency 20  # faster with more parallel calls
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from openai import AsyncOpenAI


_GENERATION_PROMPT = """Generate ONE unique residential home-security scenario. Each scenario must be meaningfully different.

Requirements:
- Vary: time of day (2 AM, noon, dusk), zone (front_door, porch, driveway, backyard, garage), entity type, props, pathing.
- Include tricky edge cases: delivery vs theft, resident return vs intruder, wildlife vs person, teenager sneaking out vs burglar.
- history_brief: 1 sentence about baseline/context (e.g. "Trash day tomorrow", "Similar porch deliveries common", "No recent benign routine").
- risk_cues: only if applicable (entry_approach, entry_dwell, tamper, suspicious_presence, perimeter_progression, wildlife_near_entry, forced_entry).
- cohort: benign (clearly safe), threat (clearly dangerous), or ambiguous (could go either way).
- expected_action: alert or suppress based on cohort and cues.

Return a single JSON object. No markdown fences."""


async def _generate_one(
    client: AsyncOpenAI,
    model: str,
    index: int,
    seed: int,
) -> dict | None:
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You generate diverse home-security scenario JSON. Output only valid JSON."},
                {"role": "user", "content": f"{_GENERATION_PROMPT}\n\nGenerate scenario #{index + 1}. Seed: {seed}."},
            ],
            max_tokens=400,
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(content)
        data["scenario_id"] = data.get("scenario_id") or f"synthetic_{index:05d}"
        return data
    except Exception as e:
        print(f"  [warn] scenario {index} failed: {e}", file=sys.stderr)
        return None


async def _run(
    count: int,
    output_path: Path,
    concurrency: int,
    model: str | None,
) -> int:
    api_key = (
        settings.groq_api_key
        or settings.cerebras_api_key
        or settings.siliconflow_api_key
        or settings.together_api_key
    )
    if not api_key:
        print("Set GROQ_API_KEY, CEREBRAS_API_KEY, SILICONFLOW_API_KEY, or TOGETHER_API_KEY", file=sys.stderr)
        return 2

    if settings.groq_api_key:
        client = AsyncOpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1")
        model = model or "llama-3.3-70b-versatile"
    elif settings.cerebras_api_key:
        client = AsyncOpenAI(api_key=settings.cerebras_api_key, base_url=settings.cerebras_base_url)
        model = model or settings.cerebras_reasoning_model
    elif settings.siliconflow_api_key:
        client = AsyncOpenAI(api_key=settings.siliconflow_api_key, base_url=settings.siliconflow_base_url)
        model = model or "deepseek-ai/DeepSeek-V3.2"
    else:
        client = AsyncOpenAI(api_key=settings.together_api_key, base_url=settings.together_base_url)
        model = model or "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    generated = 0

    async def _gen(i: int):
        nonlocal generated
        async with sem:
            result = await _generate_one(client, model, i, seed=i + 42)
            if result:
                generated += 1
                if generated % 50 == 0:
                    print(f"  generated {generated}/{count}...", file=sys.stderr)
            return result

    tasks = [_gen(i) for i in range(count)]
    results = await asyncio.gather(*tasks)

    with open(output_path, "w") as f:
        for r in results:
            if r:
                f.write(json.dumps(r) + "\n")

    print(f"wrote {generated} scenarios to {output_path}")
    return 0 if generated >= count * 0.9 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic scenarios for reasoning sandbox")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--output", default="test/fixtures/eval/reasoning_sandbox/synthetic_scenarios.jsonl")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()
    return asyncio.run(_run(args.count, output_path, args.concurrency, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
