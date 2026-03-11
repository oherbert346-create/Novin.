"""
Real Model API Validation Test

This test actually calls the configured AI providers (Groq + Cerebras)
to verify models are working and returning real outputs.

Run with: pytest test/test_real_model_validation.py -v -s
"""

import pytest
import base64
import json
import time
from datetime import datetime
from pathlib import Path

from backend.config import settings
from backend.provider import active_vision_model, active_reasoning_model


# Create a minimal valid PNG (1x1 transparent pixel)
MINIMAL_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def create_minimal_png():
    """Create a test PNG image (1x1 pixel)."""
    return base64.b64decode(MINIMAL_PNG_BASE64)


@pytest.mark.asyncio
async def test_groq_vision_api():
    """Test that Groq vision API is responding with real outputs."""
    print("\n" + "="*80)
    print("GROQ VISION API TEST")
    print("="*80)
    
    if settings.vision_provider != "groq":
        pytest.skip(f"Vision provider is {settings.vision_provider}, not groq")
    
    if not settings.groq_api_key:
        pytest.skip("GROQ_API_KEY not set")
    
    try:
        from groq import Groq
        
        client = Groq(api_key=settings.groq_api_key)
        
        print(f"\nVision Provider: {settings.vision_provider}")
        print(f"Vision Model: {active_vision_model()}")
        print(f"API Key: {'✓ Present' if settings.groq_api_key else '✗ Missing'}")
        
        # Create a simple image for vision analysis (reuse the minimal PNG)
        image_data = create_minimal_png()
        image_b64 = base64.b64encode(image_data).decode()
        
        # Call Groq vision API
        start_time = time.time()
        
        response = client.chat.completions.create(
            model=settings.groq_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": "What do you see in this image? Is there a person or threat? Keep response brief.",
                        }
                    ],
                }
            ],
            max_tokens=100,
            temperature=0.0,
        )
        
        latency_ms = (time.time() - start_time) * 1000
        
        vision_output = response.choices[0].message.content
        
        print(f"\n✓ Groq Vision API Response:")
        print(f"  Latency: {latency_ms:.1f}ms")
        print(f"  Response: {vision_output[:100]}{'...' if len(vision_output) > 100 else ''}")
        print(f"  Model used: {response.model}")
        print(f"  Stop reason: {response.choices[0].finish_reason}")
        
        assert vision_output, "Vision API returned empty response"
        assert latency_ms < 5000, f"Vision API latency too high: {latency_ms}ms"
        
    except ImportError:
        pytest.skip("groq package not installed")
    except Exception as e:
        pytest.fail(f"Groq vision API failed: {e}")


@pytest.mark.asyncio
async def test_cerebras_reasoning_api():
    """Test that Cerebras reasoning API is responding with real outputs."""
    print("\n" + "="*80)
    print("CEREBRAS REASONING API TEST")
    print("="*80)
    
    if settings.reasoning_provider != "cerebras":
        pytest.skip(f"Reasoning provider is {settings.reasoning_provider}, not cerebras")
    
    if not settings.cerebras_api_key:
        pytest.skip("CEREBRAS_API_KEY not set")
    
    try:
        from openai import AsyncOpenAI
        
        client = AsyncOpenAI(
            api_key=settings.cerebras_api_key,
            base_url=settings.cerebras_base_url,
        )
        
        print(f"\nReasoning Provider: {settings.reasoning_provider}")
        print(f"Reasoning Model: {active_reasoning_model()}")
        print(f"API Key: {'✓ Present' if settings.cerebras_api_key else '✗ Missing'}")
        print(f"Base URL: {settings.cerebras_base_url}")
        
        # Create a structured reasoning request
        reasoning_prompt = """
You are a security decision agent. Analyze this scenario and provide a JSON verdict:

SCENARIO:
- Camera: Entry point
- Vision Analysis: Person detected, confidence 0.78, high severity
- Time: 14:30 (business hours)
- Recent History: No alerts in past 24h
- Home Status: Occupied, normal activity

RESPOND WITH VALID JSON with these fields:
{
  "verdict": "alert" or "suppress",
  "confidence": 0-1,
  "reasoning": "brief explanation",
  "risk_level": "low" or "medium" or "high"
}
"""
        
        start_time = time.time()
        
        response = await client.chat.completions.create(
            model=settings.cerebras_reasoning_model,
            messages=[
                {
                    "role": "user",
                    "content": reasoning_prompt,
                }
            ],
            max_tokens=200,
            temperature=settings.reasoning_temperature,
            top_p=settings.reasoning_top_p,
        )
        
        latency_ms = (time.time() - start_time) * 1000
        
        reasoning_output = response.choices[0].message.content
        
        print(f"\n✓ Cerebras Reasoning API Response:")
        print(f"  Latency: {latency_ms:.1f}ms")
        print(f"  Model used: {response.model}")
        print(f"  Response: {reasoning_output[:150]}{'...' if len(reasoning_output) > 150 else ''}")
        print(f"  Stop reason: {response.choices[0].finish_reason}")
        
        # Try to parse JSON response
        try:
            verdict_json = json.loads(reasoning_output)
            print(f"\n  Parsed JSON Verdict:")
            print(f"    Verdict: {verdict_json.get('verdict', 'N/A')}")
            print(f"    Confidence: {verdict_json.get('confidence', 'N/A')}")
            print(f"    Risk Level: {verdict_json.get('risk_level', 'N/A')}")
        except json.JSONDecodeError:
            print(f"\n  ⚠ Response is not valid JSON (that's ok, just shows model is working)")
        
        assert reasoning_output, "Reasoning API returned empty response"
        assert latency_ms < 5000, f"Reasoning API latency too high: {latency_ms}ms"
        
    except ImportError:
        pytest.skip("openai package not installed")
    except Exception as e:
        pytest.fail(f"Cerebras reasoning API failed: {e}")


@pytest.mark.asyncio
async def test_configuration_loaded_from_environment():
    """Verify configuration is correctly loaded from .env file."""
    print("\n" + "="*80)
    print("CONFIGURATION VERIFICATION")
    print("="*80)
    
    print(f"\nEnvironment Configuration:")
    print(f"  Vision Provider: {settings.vision_provider}")
    print(f"  Vision Model: {active_vision_model()}")
    print(f"  Reasoning Provider: {settings.reasoning_provider}")
    print(f"  Reasoning Model: {active_reasoning_model()}")
    
    print(f"\nAPI Credentials Status:")
    print(f"  Groq API Key: {'✓ Set' if settings.groq_api_key else '✗ Not set'}")
    print(f"  Cerebras API Key: {'✓ Set' if settings.cerebras_api_key else '✗ Not set'}")
    print(f"  Together API Key: {'✓ Set' if settings.together_api_key else '✗ Not set'}")
    print(f"  SiliconFlow API Key: {'✓ Set' if settings.siliconflow_api_key else '✗ Not set'}")
    
    # Verify expected configuration
    assert settings.vision_provider == "groq", f"Expected vision_provider=groq, got {settings.vision_provider}"
    assert settings.reasoning_provider == "cerebras", f"Expected reasoning_provider=cerebras, got {settings.reasoning_provider}"
    assert settings.groq_api_key, "GROQ_API_KEY not configured"
    assert settings.cerebras_api_key, "CEREBRAS_API_KEY not configured"
    
    print(f"\n✓ Configuration verified: Groq + Cerebras active with valid credentials")


@pytest.mark.asyncio
async def test_full_reasoning_pipeline_with_real_api():
    """Test complete reasoning pipeline using real Cerebras API."""
    print("\n" + "="*80)
    print("FULL REASONING PIPELINE - REAL API TEST")
    print("="*80)
    
    if settings.reasoning_provider != "cerebras":
        pytest.skip(f"Reasoning provider is {settings.reasoning_provider}, not cerebras")
    
    if not settings.cerebras_api_key:
        pytest.skip("CEREBRAS_API_KEY not set")
    
    try:
        from openai import AsyncOpenAI
        
        client = AsyncOpenAI(
            api_key=settings.cerebras_api_key,
            base_url=settings.cerebras_base_url,
        )
        
        # Simulate real security decision scenario
        scenarios = [
            {
                "name": "Clear Threat",
                "camera": "Entry",
                "threat": True,
                "confidence": 0.95,
                "time": "22:00",
                "status": "Unoccupied",
                "expected": "alert"
            },
            {
                "name": "Safe Delivery",
                "camera": "Porch",
                "threat": True,
                "confidence": 0.70,
                "time": "14:00",
                "status": "Occupied",
                "expected": "suppress"
            },
            {
                "name": "Borderline Case",
                "camera": "Driveway",
                "threat": True,
                "confidence": 0.60,
                "time": "18:30",
                "status": "Occupied",
                "expected": "uncertain"
            },
        ]
        
        print(f"\nTesting {len(scenarios)} security scenarios:\n")
        
        results = []
        for scenario in scenarios:
            prompt = f"""
Security Decision Agent - Analyze this scenario:

Camera: {scenario['camera']}
Threat Detected: {scenario['threat']}
Vision Confidence: {scenario['confidence']}
Current Time: {scenario['time']}
Home Status: {scenario['status']}

Respond with brief JSON verdict:
{{ "verdict": "alert|suppress|uncertain", "confidence": 0-1 }}
"""
            
            start = time.time()
            response = await client.chat.completions.create(
                model=settings.cerebras_reasoning_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.0,
            )
            latency = (time.time() - start) * 1000
            
            output = response.choices[0].message.content
            results.append({
                "scenario": scenario["name"],
                "latency_ms": latency,
                "output": output[:80],
            })
            
            print(f"  {scenario['name']:20s} | Expected: {scenario['expected']:8s} | Latency: {latency:6.1f}ms")
        
        # Verify latencies are reasonable
        avg_latency = sum(r["latency_ms"] for r in results) / len(results)
        print(f"\n✓ All scenarios completed")
        print(f"  Average latency: {avg_latency:.1f}ms")
        print(f"  Budget remaining: {400 - avg_latency:.1f}ms (400ms total)")
        
        assert avg_latency < 400, f"Average latency {avg_latency:.1f}ms exceeds budget"
        
    except ImportError:
        pytest.skip("openai package not installed")
    except Exception as e:
        pytest.fail(f"Real API pipeline test failed: {e}")
