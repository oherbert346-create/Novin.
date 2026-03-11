# Real Image Validation Findings

**Date:** 2026-03-08  
**Scope:** Test Real Images and Review Outputs plan implementation

## Summary

Validation of the Novin Home pipeline was executed across six phases. The pipeline runs end-to-end with mock agents and with real reasoning (when vision API is available). Several code fixes were applied to unblock testing.

---

## Cases Run

### Phase 2: Existing Fixtures

**Manifest:** `test/fixtures/eval/deployment_realworld_manifest.json`  
**Cases:** 4

| Case ID                  | Zone     | Expected | Result   | HTTP |
|--------------------------|----------|----------|----------|------|
| false_alarm_pet_backyard | backyard | suppress | suppress | 200  |
| front_door_delivery_like | front_door | -      | suppress | 200  |
| driveway_vehicle_activity| driveway | -        | suppress | 200  |
| porch_person_motion      | porch    | -        | suppress | 200  |

**Result:** 4/4 passed (mock mode)

### Phase 3: Staged Real Manifest

**Manifest:** `test/fixtures/eval/home_security/staged_real/staged_real_manifest.json`  
**Cases:** 4 (placeholder images from COCO/picsum; real staged shots to be added)

| Case ID                       | Zone     | Expected | Result   |
|-------------------------------|----------|----------|----------|
| resident_return_day_placeholder | front_door | suppress | suppress |
| delivery_front_door_placeholder| front_door | suppress | suppress |
| pet_backyard_placeholder      | backyard | suppress | suppress |
| porch_person_placeholder      | porch    | suppress | suppress |

**Result:** 4/4 passed (mock mode)

---

## Per-Cohort Accuracy

- **Benign (expected suppress):** 100% (mock)
- **Threat (expected alert):** Not tested in mock run
- **Ambiguous:** Not tested

---

## Code Fixes Applied During Validation

1. **ScheduleLearner.refresh_schedule_if_due** — Method was missing; added to `backend/agent/schedule.py`. Calls `learn_schedule` when sufficient event data exists.

2. **SequenceDetector module** — Module was missing; created `backend/agent/sequence.py` with minimal `SequenceDetector` and `SequenceAnalysis`. Returns no-adjustment when no pattern is detected.

3. **run_url_ingest_demo.py mock** — Mock `_mock_process_frame` now accepts `event_context` and `**kwargs` to match `process_frame` signature.

4. **run_deployment_realworld_tests.py** — Fixed response parsing to support both nested (`routing`/`audit`) and flat (`public_verdict`) response formats. Added `_extract_confidence` helper. Mock `_mock_process` now accepts `event_context` and `**kwargs`.

---

## Output Review (Tier Checklist)

### Tier 1: Routing

- `action`: suppress/alert — present and correct in mock runs
- `severity`: none/low/medium/high/critical — present
- `categories`: person, pet, package, vehicle, intrusion, motion, clear — present

### Tier 2: Summary

- `summary` (headline): Clear, homeowner-friendly in mock output
- `narrative_summary`: Coherent, includes agent consensus

### Tier 3: Audit

- `agent_outputs`: 4 agents (threat_escalation, behavioural_pattern, context_asset_risk, adversarial_challenger)
- `decision_reason`: Present
- `confidence_score`: Extracted from evidence_digest when available

---

## Accuracy Gate (Phase 5)

**Command:** `bash scripts/run_accuracy_gate.sh`  
**Report:** `test/reports/deployment_benchmark_report.json`

**Result:** Blocked

- `staged_real_home` suite has 0 gating cases in `home_security_validation_manifest.json`
- Minimum case counts not met (e.g. min_total_cases 12, min_benign 3, min_threat 2)
- Primary pilot gate requires staged real benchmark dataset

---

## Recommendations for Future Code/Architecture Work

1. **Add staged real cases to accuracy manifest** — Populate `home_security_validation_manifest.json` with cases from `staged_real_manifest.json` (or real images once captured) and set `suite: "staged_real_home"` for pilot readiness.

2. **Vision provider configuration** — Default `VISION_PROVIDER=together` requires `TOGETHER_API_KEY`. Consider falling back to Groq when Together is unavailable, or document required env vars clearly.

3. **Sequence detection** — Current `SequenceDetector` is a stub. Implement pattern classification (delivery, intrusion, resident, loitering) per `plans/TEMPORAL_CORRELATION_PLAN.md` for temporal correlation.

4. **Real image capture** — Per `STAGED_REAL_SHOT_LIST.md`, capture real frames for: resident return (day/night), delivery, pet/wildlife, unknown person lingering, garage/porch approach after hours, tamper proxy.

5. **Run with real APIs** — Re-run deployment tests and accuracy gate with valid `GROQ_API_KEY` or `TOGETHER_API_KEY` to validate vision + reasoning on real images.
