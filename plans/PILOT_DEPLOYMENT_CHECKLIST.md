# Pilot/Prototype Deployment Checklist

## Goal: Deploy fast, validate, then outreach

---

## Phase 1: Pre-Deploy (Do Now)

### 1.1 Environment Setup
- [ ] Copy `.env.example` to `.env`
- [ ] Add `GROQ_API_KEY=your_key`
- [ ] Set `LOCAL_API_CREDENTIAL=your-secure-random-key`
- [ ] Set `INGEST_API_KEY=your-ingest-key`

### 1.2 Config Check
```bash
# Verify .env exists and has required keys
cat .env | grep -E "GROQ_API_KEY|LOCAL_API"
```

### 1.3 Local Test
```bash
# Start locally first
docker-compose up -d backend

# Check it's running
curl http://localhost:8000/health

# Stop
docker-compose down
```

---

## Phase 2: Deploy (One Command)

```bash
cd novin-home
./scripts/deploy.sh deploy
```

### What Happens
1. Builds Docker container
2. Creates backup checkpoint hook (if configured)
3. Starts backend + frontend
4. Runs readiness and smoke gates
5. Returns explainable status output

### Verify Success
- [ ] Status endpoint returns `active_streams: 0`
- [ ] Readiness check returns all checks healthy
- [ ] No errors in logs

---

## Phase 3: Functional Tests (Run These)

### 3.1 Ingest Test
```bash
# Send a test image
python scripts/run_url_ingest_demo.py
```
Expected: Returns verdict with action (alert/suppress)

### 3.2 WebSocket Test
```bash
# Check WebSocket endpoint (in browser console)
ws = new WebSocket('ws://localhost:8000/api/ws/events?api_key=YOUR_KEY')
ws.onmessage = (e) => console.log(JSON.parse(e.data))
```
Expected: Receives events in real-time

### 3.3 API Key Test
```bash
# Should fail without key
curl http://localhost:8000/api/events
# Expected: 401

# Should work with key
curl http://localhost:8000/api/events -H "x-api-key: YOUR_KEY"
# Expected: 200 with events array
```

---

## Phase 4: Validation Criteria (Pilot Ready?)

### Must Pass (P0)

| Test | Command | Expected |
|------|---------|----------|
| Service starts | `./scripts/deploy.sh deploy` | Exit code 0 |
| Readiness check | `curl /health/ready` | `{"status":"ok","checks":...}` |
| Status endpoint | `curl /api/status` | JSON with vision_model |
| Ingest works | Run demo script | Returns verdict |
| No crashes | `docker-compose logs` | No ERROR/EXCEPTION |

### Should Pass (P1)

| Test | Command | Expected |
|------|---------|----------|
| Events persist | POST event, GET events | Event in list |
| WebSocket works | Connect WS | Receives events |
| Sequence detection | 2+ events on 1 cam | sequence_id set |
| Threshold works | Set ALERT_THRESHOLD=0.80 | Fewer alerts |

### Nice to Have (P2)

| Test | Expected |
|------|----------|
| Schedule learns | After 50 events, schedule created |
| Frontend loads | http://localhost:5173 works |
| Notifications fire | Webhook receives alert |

---

## Phase 5: Outreach Ready

### When P0 + P1 Pass = Pilot Ready ✅

Create 1-pager for outreach:
```
🎯 Novin - AI Home Security

What: Intelligent home security that learns your patterns
How: Multi-agent AI + temporal correlation
Results: 35-55% false positive reduction

Try it: [deployment URL]
Docs: [docs link]
```

### Demo Scenario
1. Send 3 events on front_door camera (simulate delivery)
2. Show: sequence_id appears, confidence drops
3. Show: narrative explains "delivery sequence detected"

---

## Quick Commands Reference

```bash
# Deploy
./scripts/deploy.sh deploy

# Check status
./scripts/deploy.sh status

# Roll back backend to previous image
./scripts/deploy.sh rollback

# View logs
./scripts/deploy.sh logs

# Stop
./scripts/deploy.sh stop

# Run tests
pytest test/ -v

# Quick ingest test
python scripts/run_url_ingest_demo.py
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Build fails | `NOVIN_DEPLOY_NO_CACHE=1 ./scripts/deploy.sh deploy` |
| Service won't start | `docker-compose logs backend` |
| No events | Check API key matches |
| All alerts | Lower threshold: `ALERT_THRESHOLD=0.80` |
| No alerts | Raise threshold: `ALERT_THRESHOLD=0.60` |

---

## Success = Pilot Ready When:

✅ Service starts without errors  
✅ Health check passes  
✅ Can ingest events  
✅ Events persist in database  
✅ WebSocket pushes events  
✅ Logs are clean  

**Then you're ready for outreach!**
