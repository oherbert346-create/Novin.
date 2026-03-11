# Novin Home

Home security monitoring variant of Novin — same architecture, tuned for residential use.

## Differences from Novin (Enterprise)

### Categories (Vision & Ingest)

| Enterprise (Novin) | Home (Novin Home) |
|--------------------|-------------------|
| intrusion, crowd, object, behaviour, clear | person, pet, package, vehicle, intrusion, motion, clear |

- **person**: Human detected (distinguish from pet)
- **pet**: Animal/pet (dog, cat) — typically non-threatening
- **package**: Package or delivery at door
- **vehicle**: Car, truck in driveway/street
- **intrusion**: Unauthorised entry, forced entry, trespassing
- **motion**: General motion without clear classification

### Default Zones

- **site_id**: `home` (was `default`)
- **zone**: `front_door` (was `general`)

Supported zones: `front_door`, `porch`, `driveway`, `backyard`, `garage`, `living_room`, `kitchen`

### Ingest

**Credentials (required):** Set `INGEST_API_KEY` or `LOCAL_API_CREDENTIAL`. All ingest requests must include `x-api-key` header. Without it, ingest returns 401.

**Formats:** Canonical JSON (`image_url` or `image_b64`), Wyze (X-Attach URL), Frigate (image_url or image_b64). For authenticated image URLs, pass `image_url_headers` (e.g. `{"Authorization": "Bearer ..."}`).

Same protocols: RTSP, RTMP, HLS, HTTP(S), local files, base64. Compatible with:

- **Wyze Bridge** (HLS/RTSP) — [docker-wyze-bridge](https://github.com/mrlt8/docker-wyze-bridge)
- **Ring/Nest** — via RTSP bridges
- **ONVIF-compatible** IP cameras

### Prompts

All reasoning agents use homeowner-friendly language and home-specific context:

- **Threat Escalation**: Intrusion, forced entry, trespassing → alert. Pets, deliveries, family → usually suppress.
- **Behavioural Pattern**: `package_delivery`, `pet_activity`, `family_routine` added to behaviour types.
- **Context & Asset Risk**: Entry-point zones (front_door, porch) = higher risk. After-hours = elevated.
- **Adversarial Challenger**: Benign explanations (resident, pet, delivery) to reduce false positives.

### Database

Default DB: `novin-home.db` (configurable via `DB_URL`)

## IDs (Memory & History)

- **home_id** = `site_id` (default `home`)
- **event_id** = Unique per event; in Verdict, webhook, Event
- **cam_id** = `stream_id` (camera identity)

Webhook payload includes `home_id`, `event_id`, `cam_id` for downstream dedup and routing.

## Webhook URLs (Per-Home)

- `WEBHOOK_URL` = default
- `WEBHOOK_URL_{home_id}` = per-home override (e.g. `WEBHOOK_URL_home`, `WEBHOOK_URL_second_home`)

## Market Standards Referenced

- **Detection types**: Person, pet, package, vehicle (Ring, Nest, Arlo, Wyze)
- **Zones**: Common residential camera placements
- **ONVIF**: Video analytics standards for IP camera interoperability
- **Matter**: Smart home interoperability (future integration)
- **Webhook response**: Return 200 quickly (<2s); platforms retry on failure

## Plan

See [docs/INGEST_AND_IDENTITY_PLAN.md](docs/INGEST_AND_IDENTITY_PLAN.md) for:
- Market research (how platforms send events, what they expect)
- Universal ingest architecture (Frigate, Wyze, Reolink adapters)
- Temporal/correlation gaps and agent identity prompts
- Implementation phases

Launch policy:
- [docs/LAUNCH_ACCURACY_POLICY.md](docs/LAUNCH_ACCURACY_POLICY.md)
- [docs/RESEARCH_BRIEF_HOME_SECURITY.md](docs/RESEARCH_BRIEF_HOME_SECURITY.md)

Launch defaults:
- backend only
- blessed stack: `siliconflow` vision + `cerebras` reasoning
- release gate: accuracy + latency
- target SLA: `p95 < 3s` ingest to verdict

## Run

Backend only:

```bash
# Backend
cd backend && uv run uvicorn backend.main:app --reload
```
