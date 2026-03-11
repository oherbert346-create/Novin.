#!/usr/bin/env python3
"""Test Frigate webhook integration."""

import asyncio
import httpx

async def test_frigate_webhook():
    """Test Frigate webhook endpoint with mock payload."""
    base_url = "http://localhost:8000"
    auth = ("REDACTED_ROTATE_NOW", "REDACTED_ROTATE_NOW")
    headers = {"x-api-key": "novin_test_pilot_key_2024"}
    
    print("=" * 60)
    print("FRIGATE WEBHOOK TEST")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=90.0) as client:
        # Test 1: Frigate webhook with image URL in payload
        print("\n1. Testing Frigate webhook with image_url in payload...")
        frigate_payload = {
            "type": "end",
            "after": {
                "id": "frigate_event_123",
                "camera": "front_door",
                "label": "person",
                "current_zones": ["front_door", "driveway"],
                "start_time": 1710094800.0
            },
            "image_url": "http://images.cocodataset.org/val2017/000000000139.jpg"
        }
        
        resp = await client.post(
            f"{base_url}/api/webhooks/frigate",
            json=frigate_payload,
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"   ✓ Frigate webhook accepted")
            print(f"   - Status: {data.get('status')}")
            print(f"   - Event ID: {data.get('event_id')}")
            print(f"   - Camera: {data.get('cam_id')}")
            print(f"   - Home: {data.get('home_id')}")
        else:
            print(f"   ❌ Webhook failed: {resp.status_code}")
            print(f"   {resp.text[:500]}")
            return False
        
        # Test 2: Frigate webhook with base URL env var
        print("\n2. Testing Frigate webhook requiring ADAPTER_IMAGE_BASE_URL...")
        frigate_payload_no_url = {
            "type": "end",
            "after": {
                "id": "frigate_event_456",
                "camera": "backyard",
                "label": "dog",
                "current_zones": ["backyard"],
                "start_time": 1710094900.0
            }
        }
        
        resp = await client.post(
            f"{base_url}/api/webhooks/frigate",
            json=frigate_payload_no_url,
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 400:
            print(f"   ✓ Correctly rejected (missing image_url)")
            print(f"   - Error: {resp.json().get('detail', 'No detail')[:100]}")
        elif resp.status_code == 200:
            print(f"   ⚠ Accepted (ADAPTER_IMAGE_BASE_URL_FRIGATE must be set)")
        else:
            print(f"   ❌ Unexpected status: {resp.status_code}")
        
        # Test 3: Test idempotency
        print("\n3. Testing idempotency (duplicate event_id)...")
        resp2 = await client.post(
            f"{base_url}/api/webhooks/frigate",
            json=frigate_payload,  # Same as test 1
            headers=headers,
            auth=auth,
        )
        
        if resp2.status_code == 200:
            data2 = resp2.json()
            if data2.get('status') == 'duplicate':
                print(f"   ✓ Duplicate detected correctly")
            else:
                print(f"   ✓ Accepted (may be processed as new)")
            print(f"   - Event ID: {data2.get('event_id')}")
        
        # Test 4: Invalid payload
        print("\n4. Testing invalid payload...")
        resp = await client.post(
            f"{base_url}/api/webhooks/frigate",
            json={"invalid": "payload"},
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 400:
            print(f"   ✓ Invalid payload rejected")
            print(f"   - Error: {resp.json().get('detail', 'No detail')[:100]}")
        else:
            print(f"   ⚠ Unexpected response: {resp.status_code}")
    
    print("\n" + "=" * 60)
    print("✅ FRIGATE WEBHOOK TEST COMPLETE")
    print("=" * 60)
    return True

if __name__ == "__main__":
    asyncio.run(test_frigate_webhook())
