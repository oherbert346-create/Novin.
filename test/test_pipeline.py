import asyncio
import base64
import httpx
from PIL import Image, ImageDraw
import io

def create_test_image():
    # Create a more suspicious image - person-like shape in restricted area
    img = Image.new('RGB', (640, 480), color = (50, 50, 50))  # Dark background
    d = ImageDraw.Draw(img)
    
    # Draw a person-like figure (head and body)
    d.ellipse([250, 100, 300, 150], fill=(200, 150, 100))  # Head
    d.rectangle([240, 150, 310, 300], fill=(100, 100, 200))  # Body
    
    # Draw a "restricted area" line
    d.line([100, 0, 100, 480], fill=(255, 0, 0), width=3)
    d.text((110, 20), "RESTRICTED", fill=(255, 255, 255))
    
    # Person is crossing the restricted line
    d.text((200, 320), "INTRUDER DETECTED", fill=(255, 0, 0))
    
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

async def main():
    b64_img = create_test_image()
    print("Sending test frame to pipeline...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                "http://localhost:8000/api/ingest/frame",
                headers={"x-api-key": "test123"},
                json={
                    "b64_frame": b64_img,
                    "stream_id": "test_stream_001",
                    "label": "Test Camera",
                    "site_id": "hq",
                    "zone": "lobby"
                }
            )
            print(f"Status Code: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                print("=== COMPLETE RESPONSE ===")
                import json
                print(json.dumps(data, indent=2))
                print("\n=== TIER 1: ROUTING (Machine-Actionable) ===")
                routing = data.get('routing', {})
                print(f"Is Threat: {routing.get('is_threat')}")
                print(f"Action: {routing.get('action')}")
                print(f"Severity: {routing.get('severity')}")
                print(f"Categories: {routing.get('categories')}")
                
                print("\n=== TIER 2: OPERATOR SUMMARY (TL;DR) ===")
                summary = data.get('summary', {})
                print(f"Headline: {summary.get('headline')}")
                print(f"Narrative:\n{summary.get('narrative')}")
                
                print("\n=== TIER 3: AUDIT TRAIL (Explainability) ===")
                audit = data.get('audit', {})
                liability = audit.get('liability_digest', {})
                print(f"Final Confidence: {liability.get('confidence_score')}")
                print(f"Decision Reasoning:\n{liability.get('decision_reasoning')}")
                
                # Show individual agent outputs
                print("\n--- Agent Outputs ---")
                for agent_output in audit.get('agent_outputs', []):
                    print(f"{agent_output['agent_id']} ({agent_output['role']}): ")
                    print(f"  Verdict: {agent_output['verdict']}")
                    print(f"  Confidence: {agent_output['confidence']}")
                    print(f"  Rationale: {agent_output['rationale']}")
                    print(f"  Chain Notes: {agent_output['chain_notes']}")
                    print()
            else:
                print(f"Error: {resp.text}")
        except Exception as e:
            print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
