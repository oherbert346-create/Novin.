#!/usr/bin/env python3
"""End-to-end pilot test with real image and metrics validation."""

import asyncio
import sys
import time
from dotenv import load_dotenv

load_dotenv()

import httpx
from backend.config import settings


async def test_e2e_ingest():
    """Test full pipeline with real COCO image."""
    print("Starting E2E pilot test...\n")
    
    # Test images from COCO dataset (known good images)
    test_cases = [
        {
            "name": "COCO person outdoor",
            "url": "http://images.cocodataset.org/val2017/000000000139.jpg",
            "expected_action": "suppress",
        },
        {
            "name": "COCO street scene",
            "url": "http://images.cocodataset.org/val2017/000000000285.jpg",
            "expected_action": "suppress",
        },
    ]
    
    # Check if Basic Auth is configured
    if settings.basic_auth_user and settings.basic_auth_pass:
        auth = (settings.basic_auth_user, settings.basic_auth_pass)
        print(f"✓ Using Basic Auth: {settings.basic_auth_user}")
    else:
        auth = None
        print("⚠ No Basic Auth configured - will fail if required")
    
    # Check if API key is configured
    api_key = settings.ingest_api_key or settings.local_api_credential
    if not api_key:
        print("❌ No INGEST_API_KEY or LOCAL_API_CREDENTIAL configured")
        return False
    
    print(f"✓ Using API Key: {api_key[:15]}...")
    print()
    
    base_url = "http://localhost:8000"
    headers = {"x-api-key": api_key}
    
    results = []
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Test health endpoint (no auth required)
        print("1. Testing /health endpoint...")
        try:
            resp = await client.get(f"{base_url}/health")
            if resp.status_code == 200:
                print(f"   ✓ Health check passed: {resp.json()}\n")
            else:
                print(f"   ❌ Health check failed: {resp.status_code}\n")
                return False
        except Exception as e:
            print(f"   ❌ Health check error: {e}\n")
            return False
        
        # Test /api/status with Basic Auth
        print("2. Testing /api/status endpoint...")
        try:
            resp = await client.get(f"{base_url}/api/status", headers=headers, auth=auth)
            if resp.status_code == 200:
                status = resp.json()
                print(f"   ✓ Status check passed")
                print(f"   - Active streams: {status.get('active_streams', 0)}")
                print(f"   - Pipeline p95: {status.get('metrics_summary', {}).get('pipeline_p95_ms', 0)}ms")
                print(f"   - Requests (1h): {status.get('metrics_summary', {}).get('requests_1h', 0)}\n")
            else:
                print(f"   ❌ Status check failed: {resp.status_code} - {resp.text}\n")
                return False
        except Exception as e:
            print(f"   ❌ Status check error: {e}\n")
            return False
        
        # Test ingest with real images
        print("3. Testing ingest pipeline with real images...\n")
        for i, test in enumerate(test_cases, 1):
            print(f"   Test {i}: {test['name']}")
            t0 = time.time()
            
            try:
                payload = {
                    "cam_id": "test_cam",
                    "home_id": "test_home",
                    "zone": "backyard",
                    "image_url": test["url"],
                    "metadata": {"test": True},
                }
                
                resp = await client.post(
                    f"{base_url}/api/novin/ingest",
                    json=payload,
                    headers=headers,
                    auth=auth,
                )
                
                elapsed_ms = (time.time() - t0) * 1000
                
                if resp.status_code == 200:
                    data = resp.json()
                    action = data.get("routing", {}).get("action", "unknown")
                    risk = data.get("routing", {}).get("risk_level", "unknown")
                    
                    passed = action == test["expected_action"]
                    status_icon = "✓" if passed else "⚠"
                    
                    print(f"   {status_icon} Response: action={action}, risk={risk}, latency={elapsed_ms:.0f}ms")
                    results.append({"test": test["name"], "passed": passed, "latency_ms": elapsed_ms})
                else:
                    print(f"   ❌ Request failed: {resp.status_code} - {resp.text}")
                    results.append({"test": test["name"], "passed": False, "latency_ms": elapsed_ms})
                
            except Exception as e:
                print(f"   ❌ Error: {e}")
                results.append({"test": test["name"], "passed": False, "latency_ms": 0})
            
            print()
        
        # Test /api/metrics endpoint
        print("4. Testing /api/metrics endpoint...")
        try:
            resp = await client.get(f"{base_url}/api/metrics", headers=headers, auth=auth)
            if resp.status_code == 200:
                metrics = resp.json()
                print(f"   ✓ Metrics endpoint accessible")
                print(f"   - Pipeline p95: {metrics['latency']['pipeline_p95_ms']}ms")
                print(f"   - Requests (1h): {metrics['throughput']['requests_1h']}")
                print(f"   - Alert rate: {metrics['actions']['alert_rate_1h']}%\n")
            else:
                print(f"   ⚠ Metrics endpoint failed: {resp.status_code}\n")
        except Exception as e:
            print(f"   ⚠ Metrics endpoint error: {e}\n")
    
    # Summary
    print("=" * 60)
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    avg_latency = sum(r["latency_ms"] for r in results) / max(total, 1)
    
    print(f"E2E Test Summary:")
    print(f"  Passed: {passed}/{total} ({passed/max(total,1)*100:.0f}%)")
    print(f"  Avg latency: {avg_latency:.0f}ms")
    print("=" * 60)
    
    return passed == total


async def main():
    success = await test_e2e_ingest()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
