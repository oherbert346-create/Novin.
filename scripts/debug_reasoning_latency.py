#!/usr/bin/env python3
"""
Debug script for reasoning API latency.
Tests Cerebras and Groq with timing breakdown to find bottlenecks.

Usage:
  REASONING_PROVIDER=cerebras python scripts/debug_reasoning_latency.py
  REASONING_PROVIDER=groq GROQ_REASONING_MODEL=openai/gpt-oss-20b python scripts/debug_reasoning_latency.py
"""

import asyncio
import json
import os
import time
from pathlib import Path

# Load env before importing backend
from dotenv import load_dotenv
load_dotenv()

from backend.agent.reasoning.base import _extract_json_content
from backend.config import settings
from backend.provider import active_reasoning_model


async def test_cerebras_raw():
    """Direct Cerebras API call with timing - no reasoning_effort, no response_format."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.cerebras_api_key,
        base_url=settings.cerebras_base_url,
        timeout=30.0,
    )
    model = active_reasoning_model()
    prompt = 'Respond with JSON only: {"verdict":"suppress","risk_level":"low","confidence":0.9,"rationale":"test","recommended_action":"ignore"}'

    print(f"\n--- Cerebras raw (no reasoning_effort, no response_format) ---")
    print(f"Model: {model}")

    t0 = time.perf_counter()
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=150,
        temperature=0.0,
    )
    t1 = time.perf_counter()
    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)

    print(f"Latency: {(t1-t0)*1000:.0f}ms")
    if usage:
        print(f"Tokens: input={getattr(usage,'prompt_tokens',0)} output={getattr(usage,'completion_tokens',0)}")
    print(f"Content length: {len(content)} chars")
    print(f"Content preview: {content[:200]}...")
    return (t1 - t0) * 1000


async def test_cerebras_with_reasoning_low():
    """Cerebras with reasoning_effort=low (current pipeline config)."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.cerebras_api_key,
        base_url=settings.cerebras_base_url,
        timeout=30.0,
    )
    model = active_reasoning_model()
    prompt = 'Respond with JSON only: {"verdict":"suppress","risk_level":"low","confidence":0.9,"rationale":"test","recommended_action":"ignore"}'

    print(f"\n--- Cerebras with reasoning_effort=low ---")
    print(f"Model: {model}")

    t0 = time.perf_counter()
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=settings.cerebras_max_completion_tokens,
        temperature=0.0,
        reasoning_effort="low",
        response_format={"type": "json_object"},
    )
    t1 = time.perf_counter()
    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)

    print(f"Latency: {(t1-t0)*1000:.0f}ms")
    if usage:
        print(f"Tokens: input={getattr(usage,'prompt_tokens',0)} output={getattr(usage,'completion_tokens',0)}")
    print(f"Content length: {len(content)} chars")
    print(f"Content: {content[:300]}...")
    return (t1 - t0) * 1000


async def test_groq_reasoning_model():
    """Groq reasoning probe mirroring the runtime JSON path for each model family."""
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key)
    model = active_reasoning_model()
    prompt = 'Respond with JSON only: {"verdict":"suppress","risk_level":"low","confidence":0.9,"rationale":"test","recommended_action":"ignore"}'

    print(f"\n--- Groq {model} reasoning probe ---")

    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": settings.groq_reasoning_max_tokens,
        "temperature": 0.0,
    }
    if "gpt-oss" in model:
        kwargs["response_format"] = {"type": "json_object"}
        kwargs["extra_body"] = {"reasoning_format": "hidden", "reasoning_effort": "low"}

    t0 = time.perf_counter()
    try:
        resp = await client.chat.completions.create(**kwargs)
        t1 = time.perf_counter()
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)

        print(f"Latency: {(t1-t0)*1000:.0f}ms")
        if usage:
            print(f"Tokens: input={getattr(usage,'prompt_tokens',0)} output={getattr(usage,'completion_tokens',0)}")
        print(f"Content length: {len(content)} chars")
        print(f"Content: {content[:300]}")
        try:
            data = json.loads(_extract_json_content(content))
            print(f"Parsed JSON: verdict={data.get('verdict')}")
        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
        return (t1 - t0) * 1000
    except Exception as e:
        t1 = time.perf_counter()
        print(f"Error after {(t1-t0)*1000:.0f}ms: {e}")
        raise


async def main():
    print("=" * 60)
    print("REASONING LATENCY DEBUG")
    print("=" * 60)
    print(f"Provider: {settings.reasoning_provider}")
    print(f"Model: {active_reasoning_model()}")

    if settings.reasoning_provider == "cerebras":
        if not settings.cerebras_api_key:
            print("CEREBRAS_API_KEY not set")
            return
        await test_cerebras_raw()
        await test_cerebras_with_reasoning_low()
    elif settings.reasoning_provider == "groq":
        if not settings.groq_api_key:
            print("GROQ_API_KEY not set")
            return
        await test_groq_reasoning_model()


if __name__ == "__main__":
    asyncio.run(main())
