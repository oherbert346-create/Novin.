# Integration Guide

This guide covers all supported integration methods for ingesting camera events into the Novin Home security system.

---

## Authentication

All `/api/*` endpoints require:
1. **Basic HTTP Auth**: `Authorization: Basic <base64(username:password)>`
2. **API Key**: `x-api-key: <your-key>` header

```bash
# Example credentials (from .env)
USERNAME="pilot_user_AIOmVjQBUko"
PASSWORD="Et-01LocQpkCq3K7uC0q7laFp2ylGEEHhabUO-BxYDI"
API_KEY="novin_test_pilot_key_2024"
```

---

## Integration Methods

### 1. Direct Image URL Ingest

The simplest method - send an image URL and metadata.

**Endpoint**: `POST /api/novin/ingest`

```bash
curl -u $USERNAME:$PASSWORD \
  -X POST http://localhost:8000/api/novin/ingest \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cam_id": "front_door",
    "home_id": "my_home",
    "zone": "front_door",
    "image_url": "http://images.cocodataset.org/val2017/000000000139.jpg"
  }'
```

**Response**:
```json
{
  "event_id": "uuid",
  "routing": {
    "action": "suppress",
    "risk_level": "low"
  },
  "summary": {
    "headline": "Person standing in kitchen area"
  },
  "telemetry": {
    "pipeline_latency_ms": 1716
  }
}
```

**Query Parameters**:
- `?async=true` (default): Returns immediately, processes in background
- `?async=false`: Waits for pipeline to complete, returns full verdict

---

### 2. Base64 Image Ingest

Send base64-encoded image directly (no URL fetch required).

```bash
# Encode image to base64
IMAGE_B64=$(base64 -i /path/to/image.jpg)

curl -u $USERNAME:$PASSWORD \
  -X POST http://localhost:8000/api/novin/ingest \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"cam_id\": \"front_door\",
    \"home_id\": \"my_home\",
    \"zone\": \"front_door\",
    \"image_b64\": \"$IMAGE_B64\"
  }"
```

---

### 3. Frigate NVR Webhook

Frigate is an open-source NVR with object detection. Configure Frigate to send webhooks on motion events.

**Endpoint**: `POST /api/webhooks/frigate`

**Frigate Configuration** (`config.yml`):
```yaml
mqtt:
  enabled: true
  host: mqtt.local

detectors:
  cpu1:
    type: cpu

cameras:
  front_door:
    mqtt:
      enabled: true
      timestamp: false
    
# Use mqttwarn or Node-RED to forward MQTT events to webhook
```

**Webhook Payload**:
```json
{
  "type": "end",
  "after": {
    "id": "1710094800.123456-abc123",
    "camera": "front_door",
    "label": "person",
    "current_zones": ["front_door", "driveway"],
    "start_time": 1710094800.0
  },
  "image_url": "http://frigate.local:5000/api/events/1710094800.123456-abc123/snapshot.jpg"
}
```

**Example**:
```bash
curl -u $USERNAME:$PASSWORD \
  -X POST http://localhost:8000/api/webhooks/frigate \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "end",
    "after": {
      "id": "event_123",
      "camera": "front_door",
      "label": "person",
      "current_zones": ["front_door"]
    },
    "image_url": "http://frigate.local/api/events/event_123/snapshot.jpg"
  }'
```

**Alternative** (without image_url in payload):
Set `ADAPTER_IMAGE_BASE_URL_FRIGATE` environment variable:
```bash
ADAPTER_IMAGE_BASE_URL_FRIGATE=http://frigate.local:5000
```

Then Frigate adapter will construct image URL as: `{base}/api/events/{event_id}/snapshot.jpg`

---

### 4. Wyze Bridge Webhook

[Wyze Bridge](https://github.com/mrlt8/docker-wyze-bridge) provides RTSP streams and webhooks for Wyze cameras.

**Endpoint**: `POST /api/webhooks/wyze`

**Wyze Bridge Configuration**:
```yaml
# docker-compose.yml
services:
  wyze-bridge:
    image: mrlt8/wyze-bridge
    environment:
      - WEBHOOK_URL=http://novin-home:8000/api/webhooks/wyze
      - WYZE_EMAIL=your@email.com
      - WYZE_PASSWORD=yourpassword
```

**Webhook Headers**:
- `X-Camera`: Camera name/ID (e.g., "Front Door Cam")
- `X-Attach`: Image URL (snapshot URL)
- `X-Event`: Event type (e.g., "motion", "person")

**Webhook Body**: Text message (optional)
```
Motion detected on Front Door Cam at 14:30:00
```

**Example**:
```bash
curl -u $USERNAME:$PASSWORD \
  -X POST http://localhost:8000/api/webhooks/wyze \
  -H "x-api-key: $API_KEY" \
  -H "X-Camera: Front Door Cam" \
  -H "X-Attach: http://wyze-bridge.local/snapshot/front-door.jpg" \
  -H "X-Event: motion" \
  -d "Motion detected at 14:30:00"
```

**Zone Inference**:
The Wyze adapter automatically infers zones from camera names:
- `"Front Door Cam"` → `front_door`
- `"Backyard Camera"` → `backyard`
- `"Driveway Cam"` → `driveway`
- `"Living Room Cam"` → `living_room`
- `"Kitchen Monitor"` → `kitchen`
- Default: `front_door`

---

### 5. Generic Webhook

For any system that can send HTTP webhooks with image URLs.

**Endpoint**: `POST /api/webhooks/generic`

**Payload**:
```json
{
  "cam_id": "camera_1",          // Required
  "home_id": "my_home",          // Optional, defaults to "home"
  "zone": "front_door",          // Optional
  "image_url": "http://...",     // Required (or image_b64)
  "image_b64": "base64...",      // Alternative to image_url
  "label": "motion",             // Optional
  "metadata": {                  // Optional
    "confidence": 0.95,
    "source": "custom_system"
  }
}
```

**Example**:
```bash
curl -u $USERNAME:$PASSWORD \
  -X POST http://localhost:8000/api/webhooks/generic \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cam_id": "custom_cam_1",
    "home_id": "my_home",
    "zone": "backyard",
    "image_url": "https://storage.example.com/snapshots/cam1-20240310-143000.jpg",
    "metadata": {
      "trigger": "motion",
      "confidence": 0.92
    }
  }'
```

---

### 6. RTSP/HLS Video Streams

For continuous monitoring of live camera streams (RTSP, RTMP, HLS).

**Create Stream**:
```bash
curl -u $USERNAME:$PASSWORD \
  -X POST http://localhost:8000/api/streams \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "uri": "rtsp://192.168.1.100:554/stream1",
    "label": "Front Door Camera",
    "site_id": "my_home",
    "zone": "front_door"
  }'
```

**Response**:
```json
{
  "id": "stream-uuid",
  "uri": "rtsp://192.168.1.100:554/stream1",
  "label": "Front Door Camera",
  "site_id": "my_home",
  "zone": "front_door",
  "active": false,
  "created_at": "2024-03-10T14:30:00Z"
}
```

**Start Stream**:
```bash
curl -u $USERNAME:$PASSWORD \
  -X POST http://localhost:8000/api/streams/{stream_id}/start \
  -H "x-api-key: $API_KEY"
```

**Stop Stream**:
```bash
curl -u $USERNAME:$PASSWORD \
  -X POST http://localhost:8000/api/streams/{stream_id}/stop \
  -H "x-api-key: $API_KEY"
```

**Supported Stream Types**:
- `rtsp://` - RTSP cameras (most IP cameras)
- `rtmp://` - RTMP streams
- `http://.../stream.m3u8` - HLS streams (Wyze Bridge, many home cameras)
- `http://` - HTTP MJPEG streams

---

## Testing

### Quick Test with COCO Dataset Image

```bash
curl -u $USERNAME:$PASSWORD \
  -X POST "http://localhost:8000/api/novin/ingest?async=false" \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cam_id": "test_cam",
    "home_id": "test_home",
    "zone": "test_zone",
    "image_url": "http://images.cocodataset.org/val2017/000000000139.jpg"
  }' | jq
```

### Run Integration Test Suite

```bash
cd /Users/Ollie/novin-home
PYTHONPATH=/Users/Ollie/novin-home python3 scripts/test_all_integrations.py
```

Tests all integration methods:
- ✓ Direct image URL ingest
- ✓ Base64 image ingest
- ✓ Frigate webhook
- ✓ Wyze webhook
- ✓ Generic webhook
- ✓ Stream management
- ✓ Metrics endpoint

---

## Monitoring

### Check System Status

```bash
curl -u $USERNAME:$PASSWORD \
  -H "x-api-key: $API_KEY" \
  http://localhost:8000/api/status | jq
```

### View Metrics

```bash
curl -u $USERNAME:$PASSWORD \
  -H "x-api-key: $API_KEY" \
  http://localhost:8000/api/metrics | jq
```

**Response**:
```json
{
  "latency": {
    "pipeline_p50_ms": 1650,
    "pipeline_p95_ms": 1850,
    "vision_p95_ms": 650,
    "reasoning_p95_ms": 1000
  },
  "throughput": {
    "requests_1h": 45,
    "requests_24h": 320
  },
  "actions": {
    "alert_1h": 5,
    "suppress_1h": 40,
    "alert_rate_1h": 11.1
  }
}
```

### List Recent Events

```bash
curl -u $USERNAME:$PASSWORD \
  -H "x-api-key: $API_KEY" \
  "http://localhost:8000/api/events?limit=10" | jq
```

---

## Integration Examples by Platform

### Home Assistant

**automation.yaml**:
```yaml
automation:
  - alias: "Novin - Camera Motion"
    trigger:
      - platform: state
        entity_id: binary_sensor.front_door_motion
        to: "on"
    action:
      - service: rest_command.novin_ingest
        data:
          cam_id: "front_door"
          zone: "front_door"
          image_url: "{{ state_attr('camera.front_door', 'entity_picture') }}"

rest_command:
  novin_ingest:
    url: "http://novin-home:8000/api/novin/ingest"
    method: POST
    headers:
      Authorization: "Basic cGlsb3RfdXNlcl9BSU9tVmpRQlVrbzpFdC0wMUxvY1Fwa0NxM0s3dUMwcTdsYUZwMnlsR0VFSGhhYlVPLUJ4WURJ"
      x-api-key: "novin_test_pilot_key_2024"
      Content-Type: "application/json"
    payload: >
      {
        "cam_id": "{{ cam_id }}",
        "home_id": "my_home",
        "zone": "{{ zone }}",
        "image_url": "{{ image_url }}"
      }
```

### Node-RED (Frigate MQTT → Webhook)

```json
[
  {
    "id": "mqtt-in",
    "type": "mqtt in",
    "topic": "frigate/events",
    "broker": "mqtt-broker"
  },
  {
    "id": "http-request",
    "type": "http request",
    "method": "POST",
    "url": "http://novin-home:8000/api/webhooks/frigate",
    "headers": {
      "Authorization": "Basic cGlsb3RfdXNlcl9BSU9tVmpRQlVrbzpFdC0wMUxvY1Fwa0NxM0s3dUMwcTdsYUZwMnlsR0VFSGhhYlVPLUJ4WURJ",
      "x-api-key": "novin_test_pilot_key_2024"
    }
  }
]
```

### Python Script

```python
import requests
import base64

# Auth
auth = ("pilot_user_AIOmVjQBUko", "Et-01LocQpkCq3K7uC0q7laFp2ylGEEHhabUO-BxYDI")
headers = {"x-api-key": "novin_test_pilot_key_2024"}

# Ingest from URL
response = requests.post(
    "http://localhost:8000/api/novin/ingest",
    json={
        "cam_id": "my_camera",
        "home_id": "my_home",
        "zone": "front_door",
        "image_url": "http://example.com/snapshot.jpg"
    },
    headers=headers,
    auth=auth
)

print(response.json())
```

---

## Troubleshooting

### Webhook Not Processing

1. Check Basic Auth credentials are correct
2. Verify API key is set in header
3. Check server logs: `docker-compose logs -f backend`
4. Test health: `curl http://localhost:8000/health`

### Image Fetch Failing

- Verify image URL is accessible from server
- Check firewall rules allow outbound HTTP/HTTPS
- Test retry logic is working (check logs for retry attempts)

### Stream Not Starting

- Verify RTSP URL is accessible: `ffmpeg -i rtsp://... -frames:v 1 test.jpg`
- Check OpenCV supports the codec: `python3 -c "import cv2; print(cv2.getBuildInformation())"`
- Ensure no other process is using the stream

---

## Rate Limits

Currently no rate limiting is enforced. For production, consider:
- Rate limiting per API key (e.g., 100 req/min)
- Queue size limits for async processing
- Circuit breakers for failed external services

---

## Security Best Practices

1. **Use HTTPS** in production with valid certificates
2. **Rotate credentials** regularly (API keys, Basic Auth)
3. **Restrict network access** - use firewall rules to limit webhook sources
4. **Monitor for abuse** - check metrics for unusual patterns
5. **Validate webhooks** - consider adding webhook signature validation

---

## Support

- **Documentation**: `/docs` endpoint (Swagger UI)
- **Health Check**: `GET /health`
- **Status**: `GET /api/status` (requires auth)
- **Metrics**: `GET /api/metrics` (requires auth)
