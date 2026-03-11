#!/usr/bin/env python3
"""Test image URL fetching from various sources."""

import asyncio
import httpx

async def test_image_url_sources():
    """Test ingest with different image URL sources."""
    base_url = "http://localhost:8000"
    auth = ("pilot_user_AIOmVjQBUko", "Et-01LocQpkCq3K7uC0q7laFp2ylGEEHhabUO-BxYDI")
    headers = {"x-api-key": "novin_test_pilot_key_2024"}
    
    print("=" * 60)
    print("IMAGE URL SOURCE VALIDATION TEST")
    print("=" * 60)
    
    # Various image sources to test
    test_urls = [
        {
            "name": "COCO Dataset (HTTP)",
            "url": "http://images.cocodataset.org/val2017/000000000139.jpg",
            "expected": "success"
        },
        {
            "name": "COCO Dataset (HTTPS)",
            "url": "https://images.cocodataset.org/val2017/000000000285.jpg",
            "expected": "success"
        },
        {
            "name": "Picsum Photos (Random)",
            "url": "https://picsum.photos/1280/720",
            "expected": "success"
        },
        {
            "name": "Raw GitHub Content",
            "url": "https://raw.githubusercontent.com/opencv/opencv/master/samples/data/lena.jpg",
            "expected": "success"
        },
    ]
    
    async with httpx.AsyncClient(timeout=90.0) as client:
        results = []
        
        for i, test_case in enumerate(test_urls, 1):
            print(f"\n{i}. Testing: {test_case['name']}")
            print(f"   URL: {test_case['url'][:80]}...")
            
            import time
            t0 = time.time()
            
            try:
                resp = await client.post(
                    f"{base_url}/api/novin/ingest?async=false",
                    json={
                        "cam_id": f"url_test_cam_{i}",
                        "home_id": "test_home",
                        "zone": "test_zone",
                        "image_url": test_case['url'],
                    },
                    headers=headers,
                    auth=auth,
                )
                
                elapsed_ms = (time.time() - t0) * 1000
                
                if resp.status_code == 200:
                    data = resp.json()
                    action = data.get("routing", {}).get("action", "unknown")
                    latency = data.get("telemetry", {}).get("pipeline_latency_ms", 0)
                    
                    print(f"   ✓ Success: action={action}, latency={latency:.0f}ms, total={elapsed_ms:.0f}ms")
                    results.append({"test": test_case['name'], "passed": True, "latency_ms": elapsed_ms})
                else:
                    print(f"   ❌ Failed: {resp.status_code}")
                    print(f"   Error: {resp.text[:200]}")
                    results.append({"test": test_case['name'], "passed": False, "latency_ms": elapsed_ms})
            
            except Exception as e:
                print(f"   ❌ Exception: {e}")
                results.append({"test": test_case['name'], "passed": False, "latency_ms": 0})
        
        # Test retry logic with a slow/failing URL (if we had one)
        print(f"\n{len(test_urls) + 1}. Testing retry logic (404 error)...")
        resp = await client.post(
            f"{base_url}/api/novin/ingest?async=false",
            json={
                "cam_id": "retry_test_cam",
                "home_id": "test_home",
                "zone": "test_zone",
                "image_url": "https://httpbin.org/status/404",
            },
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code in [400, 500]:
            print(f"   ✓ Correctly failed with {resp.status_code}")
            print(f"   Error: {resp.json().get('detail', 'No detail')[:100]}")
        else:
            print(f"   ⚠ Unexpected status: {resp.status_code}")
    
    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    avg_latency = sum(r["latency_ms"] for r in results if r["passed"]) / max(passed, 1)
    
    print(f"IMAGE URL TEST SUMMARY:")
    print(f"  Passed: {passed}/{total} ({passed/max(total,1)*100:.0f}%)")
    print(f"  Avg latency: {avg_latency:.0f}ms")
    print("=" * 60)
    
    return passed == total

if __name__ == "__main__":
    success = asyncio.run(test_image_url_sources())
    exit(0 if success else 1)
