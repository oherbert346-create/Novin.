# Deployment Orchestration Plan

## Quick Start - Deploy in 5 Minutes

```bash
# 1. Build and start
cd novin-home
docker-compose up --build -d

# 2. Check status
curl http://localhost:8000/api/status
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Novin Pipeline                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌─────────────┐             │
│  │  Ingest  │───▶│  Vision  │───▶│  Reasoning  │             │
│  │ Adapter  │    │   AI     │    │   Agents    │             │
│  └──────────┘    └──────────┘    └──────┬──────┘             │
│                                           │                    │
│                                    ┌──────▼──────┐            │
│                                    │   Arbiter   │            │
│                                    │  (Voting)   │            │
│                                    └──────┬──────┘            │
│                                           │                    │
│         ┌─────────────────────────────────┼─────────────┐      │
│         │                                 │             │      │
│   ┌────▼─────────────┐    ┌─────────────▼────┐  ┌────▼────────┐ │
│   │ Sequence Detector │    │ Schedule Learner  │  │  History   │ │
│   │ (Delivery/      │    │ (Quiet/Peak      │  │  Context   │ │
│   │  Intrusion)     │    │  Hours)          │  │            │ │
│   └─────────────────┘    └───────────────────┘  └────────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
           │                    │                    │
           ▼                    ▼                    ▼
    ┌──────────┐       ┌──────────┐       ┌──────────┐
    │ Database  │       │WebSocket │       │ Notifier │
    │  (SQLite) │       │ (Push)   │       │(Webhook) │
    └──────────┘       └──────────┘       └──────────┘
```

---

## Orchestration Flow

### 1. Event Ingest Flow

```
[Camera/Webhook] 
      │
      ▼
[Adapter: Frigate/Wyze/Generic]
      │  - Parse payload
      │  - Extract image
      │  - Normalize to canonical
      ▼
[Ingest API]
      │  - Validate
      │  - Deduplicate (source_event_id)
      │  - Queue (async) or process (sync)
      ▼
[Process Frame]
      │
      ├─▶ [Vision AI] ──────────────────────┐
      │     (Object detection, categories)    │
      │                                      │
      ├─▶ [History Context] ────────────────┤
      │     (Recent events, baselines)        │
      │                                      │
      └────▶ [4 Reasoning Agents] ◀───────────┘
                  │
                  ├─ ThreatEscalation
                  ├─ BehaviouralPattern  
                  ├─ ContextAssetRisk
                  └─ AdversarialChallenger
                        │
                        ▼
                  [Arbiter]
                  - Weighted voting
                  - Apply threshold
                  │
                  ▼
            ┌─────────────────────────────┐
            │ Temporal Correlation Layer   │
            │  ├─ Sequence Detection      │
            │  └─ Schedule Learning      │
            └─────────────────────────────┘
                        │
                        ▼
            [Final Verdict: Alert/Suppress]
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   [Database]    [WebSocket]    [Notifications]
```

---

## Deployment Checklist

### Pre-Deploy
- [ ] Database migrations ready
- [ ] Environment variables configured
- [ ] API keys set (Groq, etc.)
- [ ] Webhook URLs configured

### Deploy
```bash
./scripts/deploy.sh deploy

# Optional full uncached rebuild
NOVIN_DEPLOY_NO_CACHE=1 ./scripts/deploy.sh deploy

# Roll back backend to previous image
./scripts/deploy.sh rollback
```

### Post-Deploy
- [ ] Readiness check: `curl http://localhost:8000/health/ready`
- [ ] Test ingest: `python scripts/run_url_ingest_demo.py`
- [ ] Verify WebSocket connection
- [ ] Check logs: `docker-compose logs -f backend`

---

## Environment Variables

```bash
# Required
GROQ_API_KEY=your_groq_key

# Optional - Already have good defaults
ALERT_THRESHOLD=0.70              # Alert threshold (was 0.55)
MIN_SEVERITY_TO_ALERT=low         # Minimum severity to alert
FRAME_MAX_WIDTH=1280             # Max image width
FRAME_JPEG_QUALITY=75            # JPEG compression

# Notifications (optional)
WEBHOOK_URL=https://your-webhook.com
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASS=your-password
ALERT_EMAIL_TO=you@email.com

# Security
LOCAL_API_CREDENTIAL=your-secure-key
INGEST_API_KEY=your-ingest-key
```

---

## New Features - How They Work

### Sequence Detection (NEW!)
**What**: Detects patterns in event sequences

**How**:
1. Every event queries last 15 minutes of events on same camera
2. Classifies pattern: delivery, intrusion, resident, loitering
3. Adjusts confidence based on pattern

**Patterns**:
| Pattern | Detection | Action |
|---------|-----------|--------|
| Delivery | person → package | -25% confidence |
| Intrusion | perimeter → interior | +35% confidence |
| Resident | known path | -30% confidence |
| Loitering | 3+ events, 5-30min | +20% confidence |

### Schedule Learning (NEW!)
**What**: Learns household activity patterns

**How**:
1. After 50+ events, builds hourly distribution
2. Identifies quiet hours (typically night)
3. Identifies peak hours (typically day)
4. Adjusts confidence based on time

**Adjustments**:
| Time | Detection | Action |
|------|-----------|--------|
| Quiet hours | <5% activity | +15% confidence |
| Peak hours | >30% activity | -20% confidence |

---

## Monitoring & Debugging

### Check Pipeline Status
```bash
curl http://localhost:8000/api/status
# Returns: active_streams, active_stream_ids, ws_connections, vision_model
```

### Check Recent Events
```bash
curl http://localhost:8000/api/events?limit=10 \
  -H "x-api-key: your-key"
```

### Check Logs
```bash
# All logs
docker-compose logs -f

# Just backend
docker-compose logs -f backend

# Just sequence detection
docker-compose logs | grep "Sequence analysis"
```

### Test Sequence Detection
```python
# Manually trigger sequence detection
# 1. Send multiple events on same camera
# 2. Check if sequence_id is set in event
curl http://localhost:8000/api/events/-event-id \
  -H "x-api-key: your-key"
# Look for: sequence_id, sequence_type, sequence_position
```

---

## Scaling for Production

### Current (Development)
- Single backend instance
- SQLite database
- In-memory queue

### Production Recommendations

| Component | Current | Production |
|----------|---------|------------|
| Backend | Single | 2-3 replicas |
| Database | SQLite | PostgreSQL |
| Queue | In-memory | Redis |
| Cache | None | Redis |
| Logging | Stdout | Datadog/CloudWatch |

### Docker Compose (Production-ish)
```yaml
services:
  backend:
    deploy:
      replicas: 2
    environment:
      - DB_URL=postgresql://user:pass@db:5432/novin
      - REDIS_URL=redis://redis:6379
  
  db:
    image: postgres:15
    volumes:
      - pgdata:/var/lib/postgresql/data
  
  redis:
    image: redis:7
```

---

## Common Issues

### Issue: Sequence detection not firing
**Cause**: Not enough recent events
**Fix**: Wait for 2+ events within 15 minutes

### Issue: Schedule not learning
**Cause**: < 50 events
**Fix**: System needs 50+ events to learn patterns

### Issue: Too many alerts
**Cause**: Alert threshold too low
**Fix**: Set `ALERT_THRESHOLD=0.80` in env

### Issue: Not getting events
**Cause**: API key mismatch
**Fix**: Check `INGEST_API_KEY` matches header

---

## Success Metrics

| Metric | Target | Measure |
|--------|--------|---------|
| False Positive Rate | < 10% | Alerts marked false_alarm / total |
| True Positive Rate | > 90% | Real events caught |
| Latency P95 | < 3s | timestamp to webhook |
| Sequence Detection | > 20% | Events with sequence_id |
| Schedule Learning | > 50% | Homes with schedules |

---

## Next Steps for Production

1. **Add more camera sources** (Reolink, UniFi)
2. **Add user feedback endpoint** for learning
3. **Add schedule learning trigger** (daily job)
4. **Add metrics/monitoring** (Prometheus)
5. **Add alerting** for system health
