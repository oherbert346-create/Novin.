# Shadow Pilot Deployment Guide

This guide covers deploying the reasoning pipeline for a shadow pilot with PostgreSQL, Basic Auth, full retry logic, and metrics monitoring.

Shadow-first rollout note:
- Use `VISION_PROVIDER=siliconflow`
- Use `REASONING_PROVIDER=cerebras`
- Use `INGEST_ASYNC_DEFAULT=false`
- Use `SHADOW_MODE=true` to suppress external homeowner-facing notifications during qualification
- Run `python3 scripts/run_shadow_qualification.py --base-url http://127.0.0.1:8000 --api-key "$INGEST_API_KEY"` to collect rollout evidence

---

## Prerequisites

- Neon PostgreSQL database (already configured)
- Groq API key for reasoning
- SiliconFlow API key for vision
- Basic Auth credentials (generate below)
- Ingest API key (generate below)

---

## 1. Environment Setup

### Generate Credentials

```bash
# Generate Basic Auth credentials
python3 scripts/generate_api_key.py --basic-auth

# Generate Ingest API key
python3 scripts/generate_api_key.py
```

### Create .env File

Copy `.env.example` to `.env` and configure:

```bash
# Database (PostgreSQL - Neon already configured)
DB_URL=postgresql+asyncpg://neondb_owner:<DB_PASSWORD>@<NEON_HOST>/neondb

# Auth - Basic HTTP Auth (use generated credentials above)
BASIC_AUTH_USER=<generated_username>
BASIC_AUTH_PASS=<generated_password>

# Ingest API key (use generated key above)
INGEST_API_KEY=novin_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Groq API (required)
GROQ_API_KEY=gsk_...

# SiliconFlow API (required for vision)
SILICONFLOW_API_KEY=sk-...
SILICONFLOW_VISION_MODEL=Qwen/Qwen2.5-VL-7B-Instruct

# Reasoning provider and model
REASONING_PROVIDER=groq
GROQ_REASONING_MODEL=qwen/qwen3-32b
GROQ_ENABLE_THINKING=false
GROQ_REASONING_MAX_TOKENS=600

# Vision provider
VISION_PROVIDER=siliconflow

# Ingest mode (sync for pilot testing)
INGEST_ASYNC_DEFAULT=false
```

---

## 2. Database Initialization

### Test PostgreSQL Connection

```bash
python3 scripts/test_postgres_connection.py
```

Expected output:
```
✓ Connected to PostgreSQL: PostgreSQL 17.8...
✓ Schema operations work: inserted and counted 1 row(s)
✅ PostgreSQL connection test PASSED
```

### Initialize Database Schema

```bash
python3 scripts/init_postgres_db.py
```

This creates all tables: `streams`, `events`, `agent_traces`, `agent_memories`, `home_threshold_configs`.

---

## 3. Start the Server

### Development Mode

```bash
cd /Users/Ollie/novin-home
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Production Mode (Docker)

```bash
# Build and start
docker-compose up -d --build

# Check logs
docker-compose logs -f backend

# Check health
curl http://localhost:8000/health
```

---

## 4. Validate Deployment

### Health Check (No Auth)

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"healthy"}`

### Status Check (With Auth)

```bash
curl -u REDACTED_ROTATE_NOW:REDACTED_ROTATE_NOW \
  -H "x-api-key: $INGEST_API_KEY" \
  http://localhost:8000/api/status
```

Expected: JSON with `reasoning_live: true`, `pipeline_p95_ms`, etc.

### Test Ingest (With Auth + API Key)

```bash
curl -u REDACTED_ROTATE_NOW:REDACTED_ROTATE_NOW \
  -X POST http://localhost:8000/api/novin/ingest \
  -H "x-api-key: $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cam_id": "test_cam",
    "home_id": "test_home",
    "zone": "backyard",
    "image_url": "http://images.cocodataset.org/val2017/000000000139.jpg"
  }'
```

Expected: JSON verdict with `action`, `risk_level`, `summary`, latency ~1000ms.

### Run Full E2E Test

```bash
python3 scripts/test_e2e_pilot.py
```

This runs 2 test cases with real COCO images and validates:
- Health endpoint accessibility
- Status endpoint with auth
- Ingest pipeline with real images
- Metrics endpoint

### Run All Integration Tests

```bash
PYTHONPATH=/Users/Ollie/novin-home python3 scripts/test_all_integrations.py
```

This comprehensive test validates:
- ✓ Direct image URL ingest
- ✓ Base64 image ingest  
- ✓ Frigate webhook
- ✓ Wyze webhook
- ✓ Generic webhook
- ✓ Stream management API
- ✓ Metrics endpoint

Expected output: `🎉 ALL TESTS PASSED - SYSTEM READY FOR PILOT`

---

## 5. Integration Options

The system supports multiple integration methods. See `docs/INTEGRATIONS.md` for full details.

### Webhook Endpoints

**Frigate NVR**:
```bash
curl -u $BASIC_AUTH_USER:$BASIC_AUTH_PASS \
  -X POST http://localhost:8000/api/webhooks/frigate \
  -H "x-api-key: $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "end",
    "after": {
      "id": "event_123",
      "camera": "front_door",
      "label": "person",
      "current_zones": ["front_door"]
    },
    "image_url": "http://frigate.local/snapshot.jpg"
  }'
```

**Wyze Bridge**:
```bash
curl -u $BASIC_AUTH_USER:$BASIC_AUTH_PASS \
  -X POST http://localhost:8000/api/webhooks/wyze \
  -H "x-api-key: $INGEST_API_KEY" \
  -H "X-Camera: Front Door Cam" \
  -H "X-Attach: http://wyze-bridge.local/snapshot.jpg" \
  -H "X-Event: motion" \
  -d "Motion detected at 14:30:00"
```

**Generic Webhook**:
```bash
curl -u $BASIC_AUTH_USER:$BASIC_AUTH_PASS \
  -X POST http://localhost:8000/api/webhooks/generic \
  -H "x-api-key: $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cam_id": "my_camera",
    "home_id": "my_home",
    "zone": "front_door",
    "image_url": "http://example.com/snapshot.jpg"
  }'
```

### Stream Management

**Create and start an RTSP/HLS stream**:
```bash
# Create stream
STREAM_RESPONSE=$(curl -s -u $BASIC_AUTH_USER:$BASIC_AUTH_PASS \
  -X POST http://localhost:8000/api/streams \
  -H "x-api-key: $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "uri": "rtsp://192.168.1.100:554/stream1",
    "label": "Front Door Camera",
    "site_id": "my_home",
    "zone": "front_door"
  }')

STREAM_ID=$(echo $STREAM_RESPONSE | jq -r '.id')

# Start stream
curl -u $BASIC_AUTH_USER:$BASIC_AUTH_PASS \
  -X POST http://localhost:8000/api/streams/$STREAM_ID/start \
  -H "x-api-key: $INGEST_API_KEY"
```

---

## 6. Monitor the Pilot

### Metrics Endpoint

```bash
curl -u $BASIC_AUTH_USER:$BASIC_AUTH_PASS \
  -H "x-api-key: $INGEST_API_KEY" \
  http://localhost:8000/api/metrics
```

Returns:
```json
{
  "latency": {
    "pipeline_p50_ms": 950.0,
    "pipeline_p95_ms": 1200.0,
    "pipeline_p99_ms": 1500.0,
    "vision_p95_ms": 450.0,
    "reasoning_p95_ms": 600.0
  },
  "throughput": {
    "requests_1h": 45,
    "requests_24h": 120,
    "requests_total": 500
  },
  "actions": {
    "alert_1h": 5,
    "suppress_1h": 40,
    "alert_rate_1h": 11.1
  },
  "errors": {
    "total_1h": 0,
    "by_type_1h": {}
  }
}
```

### Key Metrics to Watch

- **pipeline_p95_ms** — should stay under 3000ms (release budget)
- **alert_rate_1h** — typical 5-15% for home security
- **errors.total_1h** — should be 0 or very low
- **async_ingest_failures** — visible on `/api/status`, should be 0

---

## 6. Retry Logic & Resilience

All API calls now retry automatically with exponential backoff:

- **Groq API**: Retries on 429 (rate limit), 500, 503, timeout — max 3 attempts
- **Vision API**: Retries on all errors — max 3 attempts
- **Image fetch**: Retries on timeout, connection errors — max 2 attempts
- **Database**: Retries on connection errors — max 3 attempts

Logs show retry attempts:
```
WARNING: Retrying ... in 2.0 seconds as it raised RateLimitError
```

---

## 7. Security

### Authentication Layers

1. **Basic HTTP Auth** — Required for all `/api/*` endpoints
   - Username/password in `Authorization: Basic` header
   - Public endpoints (`/health`, `/docs`) excluded

2. **API Key** — Required for `/api/novin/ingest`
   - Must send `x-api-key` header
   - Enforced on top of Basic Auth

### Example Request with Both

```bash
curl -u username:password \
  -H "x-api-key: novin_xxx" \
  -X POST http://localhost:8000/api/novin/ingest \
  -H "Content-Type: application/json" \
  -d '{"cam_id":"cam1","home_id":"home","zone":"front_door","image_url":"..."}'
```

---

## 8. Timeouts Removed

All API client timeouts have been removed to allow retry logic to handle slow responses:

- Together/SiliconFlow clients: `timeout=None`
- Cerebras client: `timeout=None`
- Webhook/Slack HTTP clients: `timeout=None`
- Image fetch: `timeout=None` (default)

If a request is genuinely stuck, retry logic will eventually fail after max attempts.

---

## 9. Database Migration (SQLite → PostgreSQL)

If you have existing SQLite data to migrate:

```bash
# TODO: Create migration script
# For now, start fresh on PostgreSQL
```

PostgreSQL advantages:
- Connection pooling (10 base, 20 overflow)
- Safe for multi-worker deployments
- Better concurrent write performance
- Pre-ping health checks

---

## 10. Troubleshooting

### 401 Unauthorized on /api/status

- Check Basic Auth credentials are set in `.env`
- Verify `Authorization: Basic` header is sent
- Test with curl: `curl -u user:pass http://localhost:8000/api/status`

### PostgreSQL Connection Errors

- Verify `DB_URL` has no `?sslmode=require` query param (asyncpg handles SSL automatically)
- Test connection: `python3 scripts/test_postgres_connection.py`
- Check Neon console for connection limits

### High Latency (>3000ms)

- Check `/api/metrics` for breakdown (vision vs reasoning)
- Verify `GROQ_ENABLE_THINKING=false` (reduces reasoning tokens)
- Check Groq API status for rate limits

### Retry Loops

- Check logs for repeated retry attempts
- If vision API is down, fallback to suppress with degraded flag
- If Groq is rate-limiting, retries will back off exponentially

---

## 11. Next Steps

After shadow pilot validation:

1. **Rate limiting** — Add per-key limits when scaling beyond single home
2. **Dead-letter queue** — Persist failed async ingest events for replay
3. **Prometheus/Grafana** — Upgrade from JSON metrics to full observability
4. **Multi-worker** — Scale to 4-8 workers with PostgreSQL
5. **Circuit breaker** — Disable reasoning provider if consistently failing

---

## Success Criteria

- [ ] PostgreSQL connection test passes
- [ ] Database schema initialized
- [ ] Server starts without errors
- [ ] `/health` returns 200
- [ ] `/api/status` returns 200 with auth
- [ ] E2E test passes (2/2 tests)
- [ ] Pipeline p95 latency < 3000ms
- [ ] 0 async ingest failures
- [ ] 0 reasoning fallbacks on test images
- [ ] `/api/metrics` returns valid JSON

---

## Support

- Logs: `docker-compose logs -f backend` or `tail -f uvicorn.log`
- Health: `GET /health` (public)
- Status: `GET /api/status` (authed)
- Metrics: `GET /api/metrics` (authed)
- Database: Neon console at https://console.neon.tech
