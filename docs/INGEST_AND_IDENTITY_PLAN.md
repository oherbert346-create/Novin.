# Novin Home — Ingest, Identity & Universal Integration Plan

## 1. Market Research: How Platforms Send Events & What They Expect

### Inbound (how events reach us)

| Platform | Transport | Payload | Key IDs | Image |
|----------|-----------|---------|---------|-------|
| **Frigate** | MQTT `frigate/events` | `type`, `before`/`after`, `id`, `camera`, `label`, `start_time`, `end_time`, `current_zones`, `snapshot` (frame_time, box, score) | `id`, `camera`, `label` | API: `GET /events/{id}/snapshot.jpg` |
| **Wyze Bridge** | HTTP POST webhook | Body: `Motion detected on {cam} at hh:mm:ss` | Headers: `X-Camera`, `X-Event` | Header: `X-Attach` (image URL) |
| **Reolink** | HTTP POST webhook | `alarm.alarmTime`, `alarm.channel`, `alarm.device`, `alarm.deviceModel` | `channel`, `device` | Not in payload |
| **UniFi Protect** | HTTP POST webhook | `alarm.name`, `alarm.triggers` (key, device), `timestamp` | `device` (MAC) | Not in payload |
| **Verkada** | HTTP POST webhook | `camera_id`, `notification_type`, `created`, `image_url`, `video_url` | `camera_id`, `webhook_id` | `image_url` |
| **Nest / Google** | Pub/Sub (async) | `eventSessionId`, `eventId`, `eventThreadId`, `timestamp` | `eventId`, device ID in topic | `GenerateImage` API |
| **Ring** (unofficial) | Polling / events | `id`, `doorbot_id`, `kind`, `device_kind` | `id`, `doorbot_id` | Snapshot API |
| **Home Assistant** | Webhook trigger | `trigger.json` (arbitrary) | User-defined | Often URL in payload |

### Outbound expectations (what senders expect back)

| Requirement | Verkada | Turing AI | CompanyCam | Typical |
|-------------|---------|-----------|------------|---------|
| **Status** | 2xx | 200 | 200 | 2xx |
| **Response time** | < 2s | < 5s | — | < 2–5s |
| **Retry on fail** | 1x | 3x (429/5xx) | 10x exp backoff | Yes |
| **Body** | — | — | — | Often ignored |

**Critical:** Return 200 immediately, then process async. Do not block.

### Common canonical fields across industry

| Canonical | Frigate | Wyze | Reolink | UniFi | Verkada | Nest |
|-----------|---------|------|---------|-------|---------|------|
| `event_id` | `id` | — | — | — | `webhook_id`? | `eventId` |
| `camera_id` | `camera` | `X-Camera` | `device` | `device` | `camera_id` | device |
| `timestamp` | `start_time` | body | `alarmTime` | `timestamp` | `created` | `timestamp` |
| `label` | `label` | `X-Event` | — | `key` | `notification_type` | — |
| `image_url` | API | `X-Attach` | — | — | `image_url` | API |
| `source` | — | — | — | — | — | — |

---

## 2. Current ID Model vs Home Needs

### Current model

| Field | Purpose | Home gap |
|-------|---------|----------|
| `stream_id` | Pipeline/camera | OK; maps to cam |
| `frame_id` | Per-frame UUID | Ephemeral; not persisted as event |
| `site_id` | "Site" (enterprise) | Used as "home" — no explicit `home_id` |
| `event_id` | `Event.id` | Exists but not passed through ingest; not in FramePacket |
| `cam_id` | — | **Missing** — stream_id is overloaded |

### Required for memory, history, correlation

| ID | Purpose | Where |
|----|---------|-------|
| **home_id** | Single home; multi-home support | Top-level; maps to `site_id` or new field |
| **event_id** | Unique event; idempotency; external ref | Ingest request, FramePacket, Event, Verdict |
| **cam_id** | Camera identity; cross-cam correlation | Stream.id or explicit cam_id in Stream |
| **source_event_id** | External platform event ID (Frigate id, Ring id) | For dedup, traceability |

### Recommendation

- **home_id** = `site_id` (rename conceptually; keep `site_id` in DB for compatibility).
- **cam_id** = `stream_id` (Stream.id) — document as camera identity.
- **event_id** = Require or generate on ingest; pass through pipeline; persist in Event.
- **source_event_id** = Optional on ingest; store in Event for traceability.

---

## 3. Temporal Events & Correlation — Current State

### What agents receive today

**History summary (all agents):**
```
H: recent=3 similar=5 anomaly=0.42 baseline=0.12 top_similar=medium,low
```

**No explicit temporal data:**
- No timestamps of recent events
- No "event A at T1, event B at T2" sequence
- No "same camera, 2 min ago" narrative

**Context agent:** Gets `hour`, `after_hours` (derived from packet.timestamp).

**Behavioural agent:** Has `cross_camera_pattern` in chain_notes but **no cross-camera data** — history is same-camera + same-site similar, not "person seen on porch then driveway."

### Gaps

| Concept | Current | Needed |
|---------|---------|--------|
| **Temporal sequence** | Counts only | Timestamps, order, "N min ago" |
| **Cross-camera correlation** | Similar events by category | Same-entity across cams (needs re-ID) |
| **Event recency** | `same_camera_window_seconds` | Explicit "last event X min ago" in prompt |
| **Trajectory** | `escalation_trajectory` in chain_notes | No temporal input to support it |

### Recommendation

1. **Enrich `_history_summary`** with timestamps: e.g. `recent: [doorbell 2m ago, porch 5m ago]`.
2. **Add `_temporal_summary`** to base: `last_alert_minutes_ago`, `events_last_hour`.
3. **Cross-camera:** Phase 2 — would need re-ID or zone-based heuristics (e.g. porch → driveway within N min).

---

## 4. Identity — Deep Home-Specific Prompts

### Current prompts

- "Distinguish intruders from residents, pets, deliveries"
- "Could this be resident, pet, delivery driver, neighbour?"
- No explicit **identity** concept: no known faces, no "this is the homeowner."

### Home identity needs

| Concept | Enterprise | Home |
|---------|------------|------|
| **Known vs unknown** | N/A | Resident vs stranger |
| **Recurring** | N/A | Regular delivery driver, neighbour |
| **Pet vs person** | Both "object" | Explicit; pet usually suppress |
| **Package** | Rare | Common; usually suppress |
| **Time-of-day** | After-hours | "Expected" vs "unexpected" (e.g. 3am) |

### Deep prompt additions

1. **Identity framing**
   - "You cannot recognise faces. Treat as: known resident (routine), likely resident (context), unknown person (evaluate), pet (usually suppress), delivery (usually suppress)."
   - "Unknown person at 2am at back door = high concern. Unknown person at 2pm at front door = could be delivery."

2. **Temporal identity**
   - "Consider: Is this consistent with recent activity on this camera or other cameras? Multiple unknowns in sequence = escalate."

3. **Zone–identity**
   - "Living room: expect residents. Front door: expect deliveries, strangers. Backyard at night: unexpected person = high risk."

4. **Explicit unknowns**
   - "When you cannot determine identity, say so in rationale. Prefer 'uncertain' over 'alert' when identity is ambiguous."

---

## 5. Webhook URLs — Per-Home, Per-Camera

### Current

- Single `WEBHOOK_URL` in config
- All alerts go to one endpoint
- No per-home or per-camera routing

### Market expectation

- Home systems often have one webhook per home (e.g. Home Assistant, ntfy)
- Some want per-camera (e.g. `MOTION_WEBHOOKS_CAM1`, `MOTION_WEBHOOKS_CAM2`)
- Callbacks need to return 200 quickly

### Recommendation

| Level | Config | Example |
|-------|--------|---------|
| **Global** | `WEBHOOK_URL` | Fallback |
| **Per-home** | `WEBHOOK_URL_{home_id}` or DB | `WEBHOOK_URL_home_abc123` |
| **Per-camera** | DB `Stream.webhook_url` | Optional override |

**Config pattern:**
```
WEBHOOK_URL=https://default.com/alert
WEBHOOK_URL_home=https://my-ha.com/webhook/novin
WEBHOOK_URL_home_second_home=https://other.com/alerts
```

**DB:** Add `webhook_url` (nullable) to `Stream`; add `webhook_url` to home/site config if we add a Home model.

**Response:** Return 200 within 500ms; enqueue processing; process async.

---

## 6. Universal Ingest Architecture

### Design principles

1. **Accept multiple formats** — Adapters normalise to canonical internal format.
2. **Return 200 fast** — Queue work; process async.
3. **Preserve source IDs** — `source_event_id`, `source_camera_id`, `source` (frigate, wyze, etc.).
4. **Idempotency** — `source_event_id` + `source` for dedup.

### Canonical ingest payload (internal)

```json
{
  "home_id": "home_abc",
  "cam_id": "cam_front_door",
  "event_id": "evt_xyz",
  "source_event_id": "frigate_123",
  "source": "frigate",
  "timestamp": "2025-03-03T12:00:00Z",
  "image_url": "https://...",
  "image_b64": "...",
  "label": "person",
  "zone": "front_door",
  "metadata": {}
}
```

At least one of `image_url` or `image_b64` required. Fetch from URL if only URL given.

### Adapter endpoints

| Endpoint | Source | Normaliser |
|----------|--------|------------|
| `POST /api/ingest/frame` | Direct (current) | Already canonical |
| `POST /api/ingest/frigate` | Frigate MQTT → bridge | Map id→event_id, camera→cam_id, fetch snapshot |
| `POST /api/ingest/wyze` | Wyze Bridge webhook | Parse headers, fetch X-Attach |
| `POST /api/ingest/reolink` | Reolink webhook | Map device→cam_id, no image |
| `POST /api/ingest/generic` | Unknown | Accept canonical JSON |

### Ingest flow

```
[External] POST /api/ingest/{source}
     │
     ▼
[Adapter] Parse → Normalise → Canonical payload
     │
     ▼
[Queue] Push to async queue (in-memory or Redis)
     │
     ▼
[Response] 200 { "event_id": "...", "status": "queued" }
     │
     ▼ (async)
[Worker] Fetch image if URL → process_frame → persist → notify
```

---

## 7. Implementation Phases

### Phase 1 — IDs & webhooks (ship fast)

1. Add `home_id` to schemas (alias or replace `site_id` in docs).
2. Pass `event_id` through: generate if missing; include in FramePacket, Verdict, Event.
3. Add `source_event_id`, `source` to ingest API and Event (nullable).
4. Fix notifier bug: `verdict.severity` → `verdict.routing.severity`.
5. Support `WEBHOOK_URL` + `WEBHOOK_URL_{home_id}`; resolve at dispatch.
6. Return 200 quickly from ingest; process in background (or keep sync if latency OK).

### Phase 2 — Universal ingest

1. Add `POST /api/ingest/frigate` — accept Frigate event JSON; fetch snapshot; normalise.
2. Add `POST /api/ingest/wyze` — accept Wyze webhook (headers + body); fetch X-Attach.
3. Add `POST /api/ingest/generic` — accept canonical JSON.
4. Shared normaliser → canonical payload → `process_frame`.
5. Idempotency: check `(source, source_event_id)` before processing.

### Phase 3 — Temporal & correlation

1. Enrich `_history_summary` with timestamps.
2. Add `_temporal_summary` (last_alert_minutes_ago, etc.).
3. Include temporal summary in all agent prompts.
4. (Optional) Cross-camera heuristics (zone + time) without full re-ID.

### Phase 4 — Identity prompts

1. Add identity framing to all 4 reasoning agents.
2. Add "unknown person" vs "likely resident" guidance.
3. Add zone–identity rules (living room, front door, backyard).
4. Add explicit "when identity ambiguous → uncertain" rule.

---

## 8. File Change Summary

| Phase | Files |
|-------|-------|
| **1** | `schemas.py`, `db.py`, `ingest.py`, `hub.py`, `notifier.py`, `config.py`, `pipeline.py` |
| **2** | `api/ingest.py` (adapters), new `ingest/adapters/`, `ingest/normaliser.py` |
| **3** | `reasoning/base.py` (`_history_summary`, `_temporal_summary`), `history.py` |
| **4** | `reasoning/*.py` (all 4 agents), `vision.py` |

---

## 9. Webhook Payload (Outbound) — Include IDs

Current webhook sends full Verdict. Ensure it includes:

```json
{
  "home_id": "...",
  "event_id": "...",
  "cam_id": "...",
  "frame_id": "...",
  "timestamp": "...",
  "routing": { "action": "alert", "severity": "...", "categories": [...] },
  "summary": { "headline": "...", "narrative": "..." },
  "audit": { ... },
  "source_event_id": "...",
  "source": "frigate"
}
```

Downstream (HA, ntfy, custom) can use `event_id` for dedup and `cam_id` for routing.
