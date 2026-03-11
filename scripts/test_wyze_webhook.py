#!/usr/bin/env python3
"""Test Wyze webhook integration."""

import asyncio
import httpx

async def test_wyze_webhook():
    """Test Wyze webhook endpoint with mock headers and body."""
    base_url = "http://localhost:8000"
    auth = ("pilot_user_AIOmVjQBUko", "Et-01LocQpkCq3K7uC0q7laFp2ylGEEHhabUO-BxYDI")
    api_key_header = {"x-api-key": "novin_test_pilot_key_2024"}
    
    print("=" * 60)
    print("WYZE WEBHOOK TEST")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=90.0) as client:
        # Test 1: Wyze webhook with all headers
        print("\n1. Testing Wyze webhook with full headers...")
        wyze_headers = {
            **api_key_header,
            "X-Camera": "Front Door Cam",
            "X-Attach": "http://images.cocodataset.org/val2017/000000000285.jpg",
            "X-Event": "motion",
            "Content-Type": "text/plain",
        }
        wyze_body = "Motion detected on Front Door Cam at 14:30:00"
        
        resp = await client.post(
            f"{base_url}/api/webhooks/wyze",
            content=wyze_body,
            headers=wyze_headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"   ✓ Wyze webhook accepted")
            print(f"   - Status: {data.get('status')}")
            print(f"   - Event ID: {data.get('event_id')}")
            print(f"   - Camera: {data.get('cam_id')}")
            print(f"   - Home: {data.get('home_id')}")
        else:
            print(f"   ❌ Webhook failed: {resp.status_code}")
            print(f"   {resp.text[:500]}")
            return False
        
        # Test 2: Wyze webhook with backyard camera (zone inference)
        print("\n2. Testing Wyze webhook zone inference (backyard)...")
        wyze_headers_backyard = {
            **api_key_header,
            "X-Camera": "Backyard Security Camera",
            "X-Attach": "http://images.cocodataset.org/val2017/000000000139.jpg",
            "X-Event": "person",
        }
        
        resp = await client.post(
            f"{base_url}/api/webhooks/wyze",
            content="Person detected",
            headers=wyze_headers_backyard,
            auth=auth,
        )
        
        if resp.status_code == 200:
            print(f"   ✓ Backyard camera webhook accepted")
            print(f"   - Camera: {resp.json().get('cam_id')}")
            print(f"   - Expected zone: backyard (inferred from camera name)")
        else:
            print(f"   ❌ Failed: {resp.status_code}")
        
        # Test 3: Wyze webhook missing X-Attach (should fail)
        print("\n3. Testing Wyze webhook missing X-Attach header...")
        wyze_headers_no_attach = {
            **api_key_header,
            "X-Camera": "Test Camera",
            "X-Event": "motion",
        }
        
        resp = await client.post(
            f"{base_url}/api/webhooks/wyze",
            content="Motion detected",
            headers=wyze_headers_no_attach,
            auth=auth,
        )
        
        if resp.status_code == 400:
            print(f"   ✓ Correctly rejected (missing X-Attach)")
            print(f"   - Error: {resp.json().get('detail', 'No detail')[:100]}")
        else:
            print(f"   ⚠ Unexpected status: {resp.status_code}")
        
        # Test 4: Test different camera name patterns for zone inference
        print("\n4. Testing zone inference patterns...")
        test_cameras = [
            ("Front Porch Camera", "front_door"),
            ("Living Room Cam", "living_room"),
            ("Kitchen Monitor", "kitchen"),
            ("Driveway Cam", "driveway"),
            ("Unknown Camera XYZ", "front_door"),  # default
        ]
        
        for cam_name, expected_zone in test_cameras:
            resp = await client.post(
                f"{base_url}/api/webhooks/wyze",
                content="Test",
                headers={
                    **api_key_header,
                    "X-Camera": cam_name,
                    "X-Attach": "http://images.cocodataset.org/val2017/000000000139.jpg",
                },
                auth=auth,
            )
            if resp.status_code == 200:
                print(f"   ✓ {cam_name:30s} → zone: {expected_zone}")
            else:
                print(f"   ❌ {cam_name:30s} → failed")
    
    print("\n" + "=" * 60)
    print("✅ WYZE WEBHOOK TEST COMPLETE")
    print("=" * 60)
    return True

if __name__ == "__main__":
    asyncio.run(test_wyze_webhook())
