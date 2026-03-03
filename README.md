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

## Market Standards Referenced

- **Detection types**: Person, pet, package, vehicle (Ring, Nest, Arlo, Wyze)
- **Zones**: Common residential camera placements
- **ONVIF**: Video analytics standards for IP camera interoperability
- **Matter**: Smart home interoperability (future integration)

## Run

Same as Novin:

```bash
# Backend
cd backend && uv run uvicorn backend.main:app --reload

# Frontend
cd frontend && npm run dev
```
