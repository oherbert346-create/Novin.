# Launch Accuracy Policy

Version: `launch-accuracy-v1`

## Goal

Ship a backend-only residential security pilot optimized for false-positive reduction while keeping end-to-end ingest-to-verdict latency under 3 seconds at p95.

## Blessed Stack

- Vision provider/model: `siliconflow / Qwen/Qwen2.5-VL-7B-Instruct`
- Reasoning provider/model: `cerebras / gpt-oss-120b`

## Privacy Rule

- The system must not infer identity from appearance.
- It must not claim resident, guest, neighbor, family member, homeowner, or known person from image content alone.
- Trust or familiarity signals are accepted only from explicit upstream metadata or non-biometric user feedback.

## Allowed Labels

- Identity labels: `person`, `pet`, `package`, `vehicle`, `wildlife`, `clear`
- Routing categories: `person`, `pet`, `package`, `vehicle`, `intrusion`, `motion`, `clear`
- Risk labels: `entry_approach`, `entry_dwell`, `tamper`, `perimeter_progression`, `delivery_pattern`, `suspicious_presence`, `benign_activity`, `wildlife_near_entry`, `clear`

## Decision Rubric

- Alert:
  - explicit tamper or forced-entry cues
  - after-hours unknown person at an entry zone with suspicious context
  - perimeter progression or repeated unexpected presence without benign cues
- Suppress:
  - pets, packages, vehicles, benign routine motion, clear scenes
  - routine-looking person activity without explicit threat cues
- Uncertain:
  - weak evidence, contradictory evidence, poor visibility, or partial subject

## Latency Budget

- Pipeline p95: `< 3000ms`
- Vision p95: `<= 1200ms`
- Reasoning p95: `<= 1200ms`
- History/persistence/overhead p95: `<= 600ms`

## Release Gate

- Release blocks unless both pass:
  - gold-set accuracy gate with emphasis on false-positive reduction
  - p95 latency gate under 3 seconds
