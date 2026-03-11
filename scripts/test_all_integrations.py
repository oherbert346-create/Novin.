#!/usr/bin/env python3
"""Comprehensive integration test suite - all ingest sources."""

import asyncio
import sys
import httpx
import base64

async def run_test_suite():
    """Run all integration tests."""
    base_url = "http://localhost:8000"
    auth = ("pilot_user_AIOmVjQBUko", "Et-01LocQpkCq3K7uC0q7laFp2ylGEEHhabUO-BxYDI")
    headers = {"x-api-key": "novin_test_pilot_key_2024"}
    
    print("=" * 80)
    print(" " * 20 + "COMPREHENSIVE INTEGRATION TEST SUITE")
    print("=" * 80)
    
    results = []
    
    async with httpx.AsyncClient(timeout=90.0) as client:
        
        # Test 1: Direct Image URL via /api/novin/ingest
        print("\n[1/7] Testing direct image URL ingest...")
        try:
            resp = await client.post(
                f"{base_url}/api/novin/ingest?async=false",
                json={
                    "cam_id": "test_url_cam",
                    "home_id": "test_home",
                    "zone": "front_door",
                    "image_url": "http://images.cocodataset.org/val2017/000000000139.jpg"
                },
                headers=headers,
                auth=auth,
            )
            if resp.status_code == 200:
                data = resp.json()
                action = data.get("routing", {}).get("action", "unknown")
                print(f"      ✓ PASS - Action: {action}")
                results.append(("Image URL Ingest", True))
            else:
                print(f"      ❌ FAIL - Status: {resp.status_code}")
                results.append(("Image URL Ingest", False))
        except Exception as e:
            print(f"      ❌ FAIL - Error: {e}")
            results.append(("Image URL Ingest", False))
        
        # Test 2: Base64 Image via /api/novin/ingest
        print("\n[2/7] Testing base64 image ingest...")
        try:
            # Fetch an actual image and encode it
            img_resp = await client.get("http://images.cocodataset.org/val2017/000000000285.jpg")
            if img_resp.status_code == 200:
                b64_img = base64.b64encode(img_resp.content).decode('utf-8')
                
                resp = await client.post(
                    f"{base_url}/api/novin/ingest?async=false",
                    json={
                        "cam_id": "test_b64_cam",
                        "home_id": "test_home",
                        "zone": "backyard",
                        "image_b64": b64_img
                    },
                    headers=headers,
                    auth=auth,
                )
                if resp.status_code == 200:
                    print(f"      ✓ PASS")
                    results.append(("Base64 Image Ingest", True))
                else:
                    print(f"      ❌ FAIL - Status: {resp.status_code}")
                    results.append(("Base64 Image Ingest", False))
            else:
                print(f"      ❌ FAIL - Could not fetch test image")
                results.append(("Base64 Image Ingest", False))
        except Exception as e:
            print(f"      ❌ FAIL - Error: {e}")
            results.append(("Base64 Image Ingest", False))
        
        # Test 3: Frigate Webhook
        print("\n[3/7] Testing Frigate webhook...")
        try:
            frigate_payload = {
                "type": "end",
                "after": {
                    "id": "frigate_test_123",
                    "camera": "front_door",
                    "label": "person",
                    "current_zones": ["front_door"],
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
                print(f"      ✓ PASS")
                results.append(("Frigate Webhook", True))
            else:
                print(f"      ❌ FAIL - Status: {resp.status_code}")
                results.append(("Frigate Webhook", False))
        except Exception as e:
            print(f"      ❌ FAIL - Error: {e}")
            results.append(("Frigate Webhook", False))
        
        # Test 4: Wyze Webhook
        print("\n[4/7] Testing Wyze webhook...")
        try:
            wyze_headers = {
                **headers,
                "X-Camera": "Front Door Cam",
                "X-Attach": "http://images.cocodataset.org/val2017/000000000285.jpg",
                "X-Event": "motion",
            }
            
            resp = await client.post(
                f"{base_url}/api/webhooks/wyze",
                content="Motion detected at 14:30:00",
                headers=wyze_headers,
                auth=auth,
            )
            if resp.status_code == 200:
                print(f"      ✓ PASS")
                results.append(("Wyze Webhook", True))
            else:
                print(f"      ❌ FAIL - Status: {resp.status_code}")
                results.append(("Wyze Webhook", False))
        except Exception as e:
            print(f"      ❌ FAIL - Error: {e}")
            results.append(("Wyze Webhook", False))
        
        # Test 5: Generic Webhook
        print("\n[5/7] Testing generic webhook...")
        try:
            generic_payload = {
                "cam_id": "generic_test_cam",
                "home_id": "test_home",
                "zone": "driveway",
                "image_url": "http://images.cocodataset.org/val2017/000000000139.jpg",
                "metadata": {"test": True}
            }
            
            resp = await client.post(
                f"{base_url}/api/webhooks/generic",
                json=generic_payload,
                headers=headers,
                auth=auth,
            )
            if resp.status_code == 200:
                print(f"      ✓ PASS")
                results.append(("Generic Webhook", True))
            else:
                print(f"      ❌ FAIL - Status: {resp.status_code}")
                results.append(("Generic Webhook", False))
        except Exception as e:
            print(f"      ❌ FAIL - Error: {e}")
            results.append(("Generic Webhook", False))
        
        # Test 6: Stream Management
        print("\n[6/7] Testing stream management API...")
        try:
            # Create stream
            stream_resp = await client.post(
                f"{base_url}/api/streams",
                json={
                    "uri": "rtsp://test.local/stream",
                    "label": "Test Stream",
                    "site_id": "test_home",
                    "zone": "test_zone"
                },
                headers=headers,
                auth=auth,
            )
            
            if stream_resp.status_code == 201:
                stream_id = stream_resp.json().get("id")
                
                # Delete stream
                del_resp = await client.delete(
                    f"{base_url}/api/streams/{stream_id}",
                    headers=headers,
                    auth=auth,
                )
                
                if del_resp.status_code == 204:
                    print(f"      ✓ PASS")
                    results.append(("Stream Management", True))
                else:
                    print(f"      ❌ FAIL - Delete failed: {del_resp.status_code}")
                    results.append(("Stream Management", False))
            else:
                print(f"      ❌ FAIL - Create failed: {stream_resp.status_code}")
                results.append(("Stream Management", False))
        except Exception as e:
            print(f"      ❌ FAIL - Error: {e}")
            results.append(("Stream Management", False))
        
        # Test 7: Metrics Endpoint
        print("\n[7/7] Testing metrics endpoint...")
        try:
            resp = await client.get(
                f"{base_url}/api/metrics",
                headers=headers,
                auth=auth,
            )
            if resp.status_code == 200:
                metrics = resp.json()
                if "latency" in metrics and "throughput" in metrics:
                    print(f"      ✓ PASS - Requests: {metrics['throughput']['requests_total']}")
                    results.append(("Metrics Endpoint", True))
                else:
                    print(f"      ❌ FAIL - Invalid metrics format")
                    results.append(("Metrics Endpoint", False))
            else:
                print(f"      ❌ FAIL - Status: {resp.status_code}")
                results.append(("Metrics Endpoint", False))
        except Exception as e:
            print(f"      ❌ FAIL - Error: {e}")
            results.append(("Metrics Endpoint", False))
    
    # Summary
    print("\n" + "=" * 80)
    print(" " * 30 + "TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "❌ FAIL"
        print(f"  {test_name:30s} {status}")
    
    print("\n" + "-" * 80)
    print(f"  Total: {passed}/{total} tests passed ({passed/total*100:.0f}%)")
    print("=" * 80)
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED - SYSTEM READY FOR PILOT")
        return True
    else:
        print(f"\n⚠️  {total - passed} TEST(S) FAILED - REVIEW NEEDED")
        return False

if __name__ == "__main__":
    success = asyncio.run(run_test_suite())
    sys.exit(0 if success else 1)
