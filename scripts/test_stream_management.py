#!/usr/bin/env python3
"""Test stream management API (create, start, stop, delete)."""

import asyncio
import httpx

async def test_stream_management():
    """Test stream CRUD operations."""
    base_url = "http://localhost:8000"
    auth = ("pilot_user_AIOmVjQBUko", "Et-01LocQpkCq3K7uC0q7laFp2ylGEEHhabUO-BxYDI")
    headers = {"x-api-key": "novin_test_pilot_key_2024"}
    
    print("=" * 60)
    print("STREAM MANAGEMENT API TEST")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=90.0) as client:
        stream_id = None
        
        # Test 1: Create stream
        print("\n1. Creating new stream...")
        stream_payload = {
            "uri": "rtsp://example.com/stream1",
            "label": "Test Stream 1",
            "site_id": "test_home",
            "zone": "front_door"
        }
        
        resp = await client.post(
            f"{base_url}/api/streams",
            json=stream_payload,
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 201:
            data = resp.json()
            stream_id = data.get("id")
            print(f"   ✓ Stream created successfully")
            print(f"   - ID: {stream_id}")
            print(f"   - URI: {data.get('uri')}")
            print(f"   - Label: {data.get('label')}")
            print(f"   - Active: {data.get('active')}")
        else:
            print(f"   ❌ Failed to create stream: {resp.status_code}")
            print(f"   {resp.text[:500]}")
            return False
        
        # Test 2: List streams
        print("\n2. Listing all streams...")
        resp = await client.get(
            f"{base_url}/api/streams",
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            streams = resp.json()
            print(f"   ✓ Listed {len(streams)} stream(s)")
            for s in streams[:3]:  # Show first 3
                print(f"   - {s.get('label')}: {s.get('uri')} (active: {s.get('active')})")
        else:
            print(f"   ❌ Failed to list streams: {resp.status_code}")
        
        # Test 3: Get specific stream
        print(f"\n3. Getting stream {stream_id}...")
        resp = await client.get(
            f"{base_url}/api/streams/{stream_id}",
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"   ✓ Stream retrieved")
            print(f"   - Label: {data.get('label')}")
            print(f"   - Zone: {data.get('zone')}")
        else:
            print(f"   ❌ Failed to get stream: {resp.status_code}")
        
        # Test 4: Start stream (note: may fail if no actual RTSP server)
        print(f"\n4. Starting stream {stream_id}...")
        resp = await client.post(
            f"{base_url}/api/streams/{stream_id}/start",
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"   ✓ Stream start requested")
            print(f"   - Active: {data.get('active')}")
        elif resp.status_code == 500:
            print(f"   ⚠ Stream start failed (expected - no real RTSP server)")
            print(f"   Error: {resp.json().get('detail', 'No detail')[:100]}")
        else:
            print(f"   ❌ Unexpected status: {resp.status_code}")
        
        # Test 5: Stop stream
        print(f"\n5. Stopping stream {stream_id}...")
        resp = await client.post(
            f"{base_url}/api/streams/{stream_id}/stop",
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"   ✓ Stream stopped")
            print(f"   - Active: {data.get('active')}")
        else:
            print(f"   ❌ Failed to stop stream: {resp.status_code}")
        
        # Test 6: Delete stream
        print(f"\n6. Deleting stream {stream_id}...")
        resp = await client.delete(
            f"{base_url}/api/streams/{stream_id}",
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 204:
            print(f"   ✓ Stream deleted successfully")
        else:
            print(f"   ❌ Failed to delete stream: {resp.status_code}")
        
        # Test 7: Verify deletion
        print(f"\n7. Verifying stream {stream_id} is deleted...")
        resp = await client.get(
            f"{base_url}/api/streams/{stream_id}",
            headers=headers,
            auth=auth,
        )
        
        if resp.status_code == 404:
            print(f"   ✓ Stream correctly not found (deleted)")
        else:
            print(f"   ⚠ Stream still exists: {resp.status_code}")
    
    print("\n" + "=" * 60)
    print("✅ STREAM MANAGEMENT TEST COMPLETE")
    print("=" * 60)
    return True

if __name__ == "__main__":
    asyncio.run(test_stream_management())
