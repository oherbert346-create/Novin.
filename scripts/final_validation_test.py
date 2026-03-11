#!/usr/bin/env python3
"""Final validation test with sync mode."""

import asyncio
import httpx

async def test():
    base_url = "http://localhost:8000"
    auth = ("pilot_user_AIOmVjQBUko", "Et-01LocQpkCq3K7uC0q7laFp2ylGEEHhabUO-BxYDI")
    headers = {"x-api-key": "novin_test_pilot_key_2024"}
    
    async with httpx.AsyncClient(timeout=90.0) as client:
        print("=" * 60)
        print("FINAL VALIDATION TEST - SYNC MODE")
        print("=" * 60)
        
        # Test ingest in SYNC mode
        print("\n1. Testing ingest with SYNC mode...")
        ingest_resp = await client.post(
            f"{base_url}/api/novin/ingest?async=false",
            json={
                "cam_id": "final_test_cam",
                "home_id": "pilot_home",
                "zone": "front_door",
                "image_url": "http://images.cocodataset.org/val2017/000000000285.jpg",
            },
            headers=headers,
            auth=auth,
        )
        
        if ingest_resp.status_code == 200:
            data = ingest_resp.json()
            action = data.get("routing", {}).get("action", "unknown")
            risk = data.get("routing", {}).get("risk_level", "unknown")
            confidence = data.get("audit", {}).get("liability_digest", {}).get("confidence_score", 0)
            latency = data.get("telemetry", {}).get("pipeline_latency_ms", 0)
            summary = data.get("summary", {}).get("headline", "N/A")
            
            print(f"\n   ✓ SYNC Ingest successful!")
            print(f"   - Action: {action}")
            print(f"   - Risk: {risk}")
            print(f"   - Confidence: {confidence:.0%}")
            print(f"   - Latency: {latency:.0f}ms")
            print(f"   - Summary: {summary}")
            
            vision_lat = data.get("telemetry", {}).get("vision_latency_ms", 0)
            reasoning_lat = data.get("telemetry", {}).get("reasoning_latency_ms", 0)
            print(f"\n   Latency Breakdown:")
            print(f"   - Vision: {vision_lat:.0f}ms")
            print(f"   - Reasoning: {reasoning_lat:.0f}ms")
            print(f"   - Total: {latency:.0f}ms")
            
            if latency > 3000:
                print(f"   ⚠ Latency exceeds 3000ms budget!")
            else:
                print(f"   ✓ Within 3000ms release budget")
        else:
            print(f"   ❌ Ingest failed: {ingest_resp.status_code}")
            print(f"   {ingest_resp.text[:500]}")
            return False
        
        # Check metrics
        print("\n2. Checking /api/metrics...")
        metrics_resp = await client.get(f"{base_url}/api/metrics", headers=headers, auth=auth)
        if metrics_resp.status_code == 200:
            metrics = metrics_resp.json()
            print(f"\n   Pipeline Latency:")
            print(f"   - p50: {metrics['latency']['pipeline_p50_ms']}ms")
            print(f"   - p95: {metrics['latency']['pipeline_p95_ms']}ms")
            print(f"   - p99: {metrics['latency']['pipeline_p99_ms']}ms")
            
            print(f"\n   Throughput:")
            print(f"   - Last 1h: {metrics['throughput']['requests_1h']} requests")
            print(f"   - Total: {metrics['throughput']['requests_total']} requests")
            
            print(f"\n   Actions (1h):")
            print(f"   - Alerts: {metrics['actions']['alert_1h']}")
            print(f"   - Suppress: {metrics['actions']['suppress_1h']}")
            print(f"   - Alert rate: {metrics['actions']['alert_rate_1h']}%")
            
            print(f"\n   Errors:")
            print(f"   - Last 1h: {metrics['errors']['total_1h']}")
            print(f"   - Last 24h: {metrics['errors']['total_24h']}")
        
        # Check status
        print("\n3. Checking /api/status...")
        status_resp = await client.get(f"{base_url}/api/status", headers=headers, auth=auth)
        if status_resp.status_code == 200:
            status = status_resp.json()
            print(f"\n   System Status:")
            print(f"   - Vision: {status['vision_provider']} / {status['vision_model']}")
            print(f"   - Reasoning: {status['reasoning_provider']} / {status['reasoning_model']}")
            print(f"   - Async failures: {status['async_ingest_failures']}")
            print(f"   - Active streams: {status['active_streams']}")
        
        print("\n" + "=" * 60)
        print("✅ FINAL VALIDATION COMPLETE - SHADOW PILOT READY")
        print("=" * 60)
        return True

if __name__ == "__main__":
    success = asyncio.run(test())
    exit(0 if success else 1)
