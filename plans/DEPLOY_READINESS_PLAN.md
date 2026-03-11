# Deploy Readiness Plan — Innovative & Accurate, Not Average

**Goal:** Ship a deployable pilot that is differentiated, accurate, and production-ready — without over-engineering.

---

## Current State

| Area | Status | Notes |
|------|--------|-------|
| **Pipeline** | ✅ Solid | Multi-agent, temporal correlation, guardrails |
| **Blessed stack** | SiliconFlow (vision) + Cerebras (reasoning) | Per `LAUNCH_ACCURACY_POLICY` |
| **Deploy script** | ⚠️ Partial | Requires GROQ even when blessed stack doesn't use it |
| **Accuracy gate** | ❌ Blocked | Manifest has 8 cases, gate needs 12+; staged real cases missing |
| **Go/No-Go** | ❌ Incomplete | Contradiction rate, explanation quality not measured |
| **Frontend** | ❌ Removed | Backend-only per README |

---

## Phase 1: Unblock Deployment (1–2 days)

### 1.1 Fix deploy preflight for blessed stack

**Problem:** `deploy.sh` requires `GROQ_API_KEY` but blessed stack is SiliconFlow + Cerebras.

**Action:** Update `check_env()` to require keys for the *active* providers:
- If `VISION_PROVIDER=siliconflow` → require `SILICONFLOW_API_KEY`
- If `REASONING_PROVIDER=cerebras` → require `CEREBRAS_API_KEY`
- Make `GROQ_API_KEY` optional when neither vision nor reasoning use Groq

### 1.2 Fix hub/pipeline client wiring

**Problem:** `PipelineManager` and ingest path assume `groq_client` is always present.

**Action:** Refactor so pipeline accepts a generic "LLM client" or uses provider-specific clients directly. Vision and reasoning already use `get_siliconflow_client()` / Cerebras internally — ensure ingest path works when `groq_client` is `None` (e.g. pass a no-op or use provider clients).

### 1.3 Verify `.env.example` matches blessed stack

- Default `VISION_PROVIDER=siliconflow`
- Default `REASONING_PROVIDER=cerebras`
- Document required keys: `SILICONFLOW_API_KEY`, `CEREBRAS_API_KEY`, `INGEST_API_KEY`

---

## Phase 2: Accuracy Gate Pass (2–3 days)

### 2.1 Expand validation manifest

**Current:** 8 cases in `home_security_validation_manifest.json`; gate needs min 12 total, 3 benign, 2 threat, 2 ambiguous, etc.

**Action:**
1. Add 4+ cases from existing fixtures (e.g. `coco_*.jpg`, `picsum_*.jpg`) with clear expected actions
2. Ensure cohort coverage: benign (suppress), threat (alert), ambiguous (either)
3. Add at least 1 temporal/sequence case if fixtures support it
4. Set `benchmark_eligibility: "gating"` for cases that count toward the gate

### 2.2 Run accuracy gate with real APIs

```bash
# Ensure blessed stack keys are set
VISION_PROVIDER=siliconflow REASONING_PROVIDER=cerebras \
  ./scripts/run_accuracy_gate.sh
```

**Targets (from `run_accuracy_gate.sh`):**
- `min_action_accuracy`: 0.70
- `max_false_alert_rate_benign`: 0.35
- `max_missed_alert_rate_threat`: 0.50
- `max_p95_latency_s`: 2.5

### 2.3 Fix brittle test (optional, quick win)

`test_wildlife_scenario_keeps_identity_separate_from_risk_and_emits_diagnostics` fails on exact headline text. Relax assertion to check for "low" or "no concern" semantics instead of literal "no home-security concern".

---

## Phase 3: Go/No-Go Evidence (1–2 days)

### 3.1 Contradiction rate

**Target:** ≤ 3% (per `PILOT_GO_NO_GO_CHECKLIST`)

**How:** Run deployment real-world tests, count verdicts where `CONSISTENCY_CHECKS` contains `warn:`.

```bash
PYTHONPATH=. uv run python scripts/run_deployment_realworld_tests.py --inprocess
# Parse output for contradiction_warnings / evaluated_verdicts
```

### 3.2 Explanation quality

**Target:** ≥ 90% pass (evidence + uncertainty + policy basis)

**How:** Sample 20–30 verdicts, score each on:
- Evidence quality: rationale has concrete observations
- Uncertainty clarity: states limits when applicable
- Policy basis: explains why alert/suppress

### 3.3 Document results

Create `docs/PILOT_GO_NO_GO_EVIDENCE.md` with:
- Contradiction rate and sample size
- Explanation quality score and rubric notes
- Screenshot or sample of `audit.liability_digest.decision_reasoning`

---

## Phase 4: Differentiated Edge (Optional, 1–2 days)

These keep you **innovative** without over-building:

### 4.1 One-pager for outreach

Per `PILOT_DEPLOYMENT_CHECKLIST` Phase 5:

```
Novin Home — AI Home Security

What: Multi-agent reasoning + temporal correlation
How: 4 specialized agents (threat, behaviour, context, adversarial) + sequence/schedule learning
Edge: 35–55% false positive reduction vs single-model; privacy-first (no identity inference)
```

### 4.2 Demo scenario script

1. Send 3 events on `front_door` (simulate delivery)
2. Show `sequence_id` and confidence drop
3. Show narrative: "delivery sequence detected"

### 4.3 Feedback loop visibility

Ensure `POST /api/events/{id}/feedback` (false_positive / false_negative) is documented and wired — this powers adaptive thresholds and is a differentiator.

---

## Phase 5: Pre-Deploy Checklist

Before `./scripts/deploy.sh deploy`:

- [ ] `.env` has `SILICONFLOW_API_KEY`, `CEREBRAS_API_KEY`, `INGEST_API_KEY` (or `LOCAL_API_CREDENTIAL`)
- [ ] `./scripts/release_test.sh` passes (or `RELEASE_USE_MOCK=1` for CI)
- [ ] `./scripts/run_accuracy_gate.sh` passes
- [ ] `curl http://localhost:8000/health/ready` returns 200
- [ ] `python scripts/run_url_ingest_demo.py` returns a verdict
- [ ] Rollback tested once: `./scripts/deploy.sh rollback`

---

## Priority Order

| Priority | Task | Effort | Blocks |
|----------|------|--------|--------|
| P0 | Fix deploy env check for blessed stack | 30 min | Deploy |
| P0 | Fix hub client when Groq not used | 1–2 hr | Deploy |
| P0 | Expand manifest to 12+ gating cases | 1–2 hr | Accuracy gate |
| P1 | Run accuracy gate, tune if needed | 1–2 hr | Release gate |
| P1 | Measure contradiction rate | 1 hr | Go/No-Go |
| P2 | Explanation quality sampling | 2 hr | Go/No-Go |
| P2 | Fix wildlife test assertion | 15 min | CI |
| P3 | One-pager + demo script | 1 hr | Outreach |

---

## Success Criteria

**Deployable:** `./scripts/deploy.sh deploy` succeeds; health check passes; ingest returns verdicts.

**Accurate:** Accuracy gate passes; contradiction rate ≤ 3%; explanation quality ≥ 90%.

**Innovative:** Multi-agent + temporal correlation + adaptive thresholds + privacy-first identity policy — already in place; document and demo it.

---

## Out of Scope (For Now)

- Frontend rebuild
- New model providers
- Multi-round agent debate
- Real staged image capture (use public dataset fixtures for pilot)
