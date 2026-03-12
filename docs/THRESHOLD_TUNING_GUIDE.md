# Threshold Tuning Guide for Pilot Operators

This guide explains how to diagnose and tune alert thresholds for each pilot home, and what to look for in the shadow logs before going live.

---

## How Thresholds Work

Every verdict passes through two confidence gates:

1. **`vote_confidence_threshold`** — minimum confidence for an individual agent vote to count
2. **`strong_vote_threshold`** — threshold for an agent vote to be considered "strong" (weighted higher in arbiter)
3. **`min_alert_confidence`** — minimum final arbiter confidence before an alert is fired
4. **Global `ALERT_THRESHOLD`** — env var applied as additional gate (default 0.70)

An alert fires only when ALL gates pass. This means **tuning is conservative by default** — you can always lower thresholds to increase sensitivity.

### Adaptive Feedback Loop

After **50+ feedback events** (user marking alerts as false positive/false negative), the system automatically adjusts per-home thresholds by ±0.05 per 24h. This is bounded to prevent runaway tuning.

**For pilot:** Thresholds are pre-seeded so the adaptive loop starts from a sensible baseline. See `scripts/seed_pilot_thresholds.py`.

---

## Step 1: Pre-Pilot Shadow Log Review

Before enabling live notifications, run in `SHADOW_MODE=true` for at least 24–48 hours and review the shadow webhook logs.

### What to look for in shadow logs

Each shadow event has this shape:
```json
{
  "shadow_mode": true,
  "action": "alert" | "suppress",
  "severity": "high" | "medium" | "low" | "none",
  "risk_level": "high" | "medium" | "low" | "none",
  "categories": ["person", "intrusion", ...],
  "description": "...",
  "summary": { "narrative": "..." },
  "agent_outputs": [...],
  "routing": { "notification_policy": "immediate" | "none" }
}
```

**Review for these patterns:**

| Pattern | What it means | Action |
|---------|--------------|--------|
| Many `action=alert` for pets/shadows/motion | Threshold too low → FP risk | Raise `ALERT_THRESHOLD` to 0.75–0.80 |
| Many `action=suppress` for clear intrusion clips | Threshold too high → FN risk | Lower `ALERT_THRESHOLD` to 0.65 |
| `severity=none` but homeowner would expect alert | Categories too restrictive | Check `MIN_SEVERITY_TO_ALERT` |
| `agent_outputs` show split votes (2 alert, 2 suppress) | Genuinely ambiguous scene | Acceptable; check confidence values |
| All agents confident but arbiter suppressed | `ALERT_THRESHOLD` too high | Lower it slightly |

### Reading agent outputs

Each event includes 4 agent verdicts:
```json
{
  "agent_id": "executive_triage_commander",
  "role": "Executive Triage",
  "verdict": "alert",
  "confidence": 0.88,
  "rationale": "Person at door at 02:14 — no package delivery context..."
}
```

- `executive_triage_commander` has 50% weight — if it says alert with high confidence, it usually fires
- `falsification_auditor` with a strong "suppress" overrides borderline alerts
- `confidence < 0.40` votes don't count toward quorum

---

## Step 2: Environment-Specific Tuning

Run the seeding script with the appropriate preset before pilot:

```bash
# Outdoor driveway or front door
PYTHONPATH=. python scripts/seed_pilot_thresholds.py --site-ids SITE_ID --preset outdoor

# Indoor camera
PYTHONPATH=. python scripts/seed_pilot_thresholds.py --site-ids SITE_ID --preset indoor

# High-crime area where sensitivity matters more than FP rate
PYTHONPATH=. python scripts/seed_pilot_thresholds.py --site-ids SITE_ID --preset high_sensitivity

# Very cautious (minimise interruptions, accept some misses)
PYTHONPATH=. python scripts/seed_pilot_thresholds.py --site-ids SITE_ID --preset conservative

# List all presets
PYTHONPATH=. python scripts/seed_pilot_thresholds.py --list-presets
```

Preset quick reference:

| Preset | vote_threshold | strong_threshold | min_alert | When to use |
|--------|---------------|-----------------|-----------|-------------|
| `default` | 0.60 | 0.75 | 0.40 | Most homes, starting point |
| `outdoor` | 0.65 | 0.80 | 0.45 | Driveway, garden, high ambient motion |
| `indoor` | 0.55 | 0.70 | 0.35 | Interior rooms |
| `high_sensitivity` | 0.50 | 0.65 | 0.30 | Needs max coverage |
| `conservative` | 0.75 | 0.85 | 0.55 | Minimise all notifications |

---

## Step 3: Flip to Live

When shadow log review shows acceptable alert quality:

1. In `.env`, change `SHADOW_MODE=false`
2. Confirm `SHADOW_WEBHOOK_URL` remains set (shadow logging continues alongside live notifications for audit trail)
3. Restart the server
4. Monitor `/api/status` → `frame_drops_by_stream` for any processing backpressure
5. Watch `alert_rate_1h` in `/api/status` → `metrics_summary`

**Expected alert rates at pilot scale (1-5 homes):**
- Residential home: 2–10 alerts/day is normal
- Driveway with road traffic: may spike to 20–30 without outdoor preset
- Night-time: most activity should suppress unless genuine threat

---

## Step 4: Post-Pilot Feedback

After the pilot collects 50+ events per home:
1. Check `/api/events?stream_id=...` for historical verdicts
2. Use the `user_feedback` field (via UI or direct API PATCH) to mark FPs and FNs
3. The adaptive threshold loop will automatically adjust ±0.05 every 24h
4. Monitor `HomeThresholdConfig` table for drift: `strong_vote_threshold` should not exceed 0.90 or drop below 0.50

---

## Common Issues

**"Too many false alarms — homeowner is frustrated"**
- Set `SHADOW_MODE=false`, then raise `ALERT_THRESHOLD` in `.env` to 0.80
- Re-seed with `--preset conservative` for the specific site
- Verify `MIN_SEVERITY_TO_ALERT=medium` to skip low-severity events

**"Missing real events"**
- Lower `ALERT_THRESHOLD` to 0.65
- Re-seed with `--preset high_sensitivity`
- Check that `explicit threat cues` (forced_entry, tamper) in vision output — these bypass all thresholds

**"Agent votes are inconsistent across similar frames"**
- Check `REASONING_TEMPERATURE=0.0` is set (deterministic mode)
- Check `REASONING_PROVIDER` — some providers have higher variance at low token budgets
- Increase `SILICONFLOW_THINKING_BUDGET` if using SiliconFlow

**"System slow to respond / timeouts"**
- Check `/api/status` → `metrics_summary.pipeline_p95_ms`
- If >2500ms regularly, switch `REASONING_PROVIDER` to Cerebras or Groq
- Check `frame_drops_by_stream` — drops indicate sustained overload
