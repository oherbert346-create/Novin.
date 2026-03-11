#!/usr/bin/env python3
"""Test generic webhook integration."""

import asyncio
import httpx

async def test_generic_webhook():
    """Test generic webhook endpoint."""
    base_url = "http://localhost:8000"
    auth = ("REDACTED_ROTATE_NOW", "REDACTED_ROTATE_NOW")
    headers = {"x-api-key": "novin_test_pilot_key_2024"}
    
    print("=" * 60)
    print("GENERIC WEBHOOK TEST")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=90.0) as client:
        # Test 1: Generic webhook with image URL
        print("\n1. Testing generic webhook with image_url...")
        payload = {
            "cam_id": "generic_cam_1",
            "home_id": "pilot_home",
            "zone": "front_door",
            "image_url": "http://images.cocodataset.org/val2017/000000000139.jpg",
            "label": "motion",
            "metadata": {
                "source": "custom_system",
                "confidence": 0.95
            }
        }
        
        resp = await client.post(
            f"{base_url}/api/webhooks/generic",
            json=payload,
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"   ✓ Generic webhook accepted")
            print(f"   - Status: {data.get('status')}")
            print(f"   - Event ID: {data.get('event_id')}")
            print(f"   - Camera: {data.get('cam_id')}")
        else:
            print(f"   ❌ Webhook failed: {resp.status_code}")
            print(f"   {resp.text[:500]}")
            return False
        
        # Test 2: Generic webhook with base64 image
        print("\n2. Testing generic webhook with image_b64...")
        import base64
        # Small 1x1 red pixel PNG
        small_png = base64.b64encode(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf'
            b'\xc0\x00\x00\x00\x03\x00\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        ).decode('utf-8')
        
        payload_b64 = {
            "cam_id": "generic_cam_2",
            "home_id": "pilot_home",
            "zone": "backyard",
            "image_b64": small_png,
            "label": "test"
        }
        
        resp = await client.post(
            f"{base_url}/api/webhooks/generic",
            json=payload_b64,
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            print(f"   ✓ Base64 image webhook accepted")
            print(f"   - Event ID: {resp.json().get('event_id')}")
        else:
            print(f"   ❌ Failed: {resp.status_code}")
            print(f"   {resp.text[:300]}")
        
        # Test 3: Missing cam_id (should fail)
        print("\n3. Testing missing cam_id...")
        resp = await client.post(
            f"{base_url}/api/webhooks/generic",
            json={
                "image_url": "http://example.com/image.jpg",
                "home_id": "home"
            },
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 400:
            print(f"   ✓ Correctly rejected (missing cam_id)")
            print(f"   - Error: {resp.json().get('detail', 'No detail')[:100]}")
        else:
            print(f"   ⚠ Unexpected status: {resp.status_code}")
        
        # Test 4: Missing both image_url and image_b64 (should fail)
        print("\n4. Testing missing image data...")
        resp = await client.post(
            f"{base_url}/api/webhooks/generic",
            json={
                "cam_id": "test_cam",
                "home_id": "home"
            },
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 400:
            print(f"   ✓ Correctly rejected (missing image)")
            print(f"   - Error: {resp.json().get('detail', 'No detail')[:100]}")
        else:
            print(f"   ⚠ Unexpected status: {resp.status_code}")
        
        # Test 5: Defaults (no home_id, no zone)
        print("\n5. Testing with defaults (no home_id, no zone)...")
        resp = await client.post(
            f"{base_url}/api/webhooks/generic",
            json={
                "cam_id": "minimal_cam",
                "image_url": "http://images.cocodataset.org/val2017/000000000285.jpg"
            },
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"   ✓ Accepted with defaults")
            print(f"   - Home ID: {data.get('home_id')} (should be 'home')")
        else:
            print(f"   ❌ Failed: {resp.status_code}")
    
    print("\n" + "=" * 60)
    print("✅ GENERIC WEBHOOK TEST COMPLETE")
    print("=" * 60)
    return True

if __name__ == "__main__":
    asyncio.run(test_generic_webhook())
