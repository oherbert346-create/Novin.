#!/usr/bin/env python3
"""Quick E2E test with stream setup."""

import asyncio
import httpx

async def test():
    base_url = "http://localhost:8000"
    auth = ("pilot_user_AIOmVjQBUko", "Et-01LocQpkCq3K7uC0q7laFp2ylGEEHhabUO-BxYDI")
    headers = {"x-api-key": "novin_test_pilot_key_2024"}
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Create stream first
        print("1. Creating stream...")
        stream_resp = await client.post(
            f"{base_url}/api/streams",
            json={
                "stream_id": "test_cam_1",
                "site_id": "test_home",
                "zone": "front_door",
                "label": "Test Camera 1",
            },
            headers=headers,
            auth=auth,
        )
        print(f"   Stream creation: {stream_resp.status_code}")
        
        # 2. Test ingest
        print("\n2. Testing ingest with real image...")
        ingest_resp = await client.post(
            f"{base_url}/api/novin/ingest",
            json={
                "cam_id": "test_cam_1",
                "home_id": "test_home",
                "zone": "front_door",
                "image_url": "http://images.cocodataset.org/val2017/000000000139.jpg",
            },
            headers=headers,
            auth=auth,
        )
        
        if ingest_resp.status_code == 200:
            data = ingest_resp.json()
            action = data.get("routing", {}).get("action", "unknown")
            risk = data.get("routing", {}).get("risk_level", "unknown")
            latency = data.get("telemetry", {}).get("pipeline_latency_ms", 0)
            print(f"   ✓ Ingest successful: action={action}, risk={risk}, latency={latency:.0f}ms")
            print(f"   Summary: {data.get('summary', 'N/A')[:80]}...")
        else:
            print(f"   ❌ Ingest failed: {ingest_resp.status_code} - {ingest_resp.text[:200]}")
        
        # 3. Check metrics
        print("\n3. Checking metrics...")
        metrics_resp = await client.get(f"{base_url}/api/metrics", headers=headers, auth=auth)
        if metrics_resp.status_code == 200:
            metrics = metrics_resp.json()
            print(f"   Pipeline p95: {metrics['latency']['pipeline_p95_ms']}ms")
            print(f"   Requests (1h): {metrics['throughput']['requests_1h']}")
            print(f"   Alert rate: {metrics['actions']['alert_rate_1h']}%")
        
        print("\n✅ E2E test complete!")

if __name__ == "__main__":
    asyncio.run(test())
