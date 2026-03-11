# Release Testing & Benchmarking

Before release, run the full pipeline against production-like config.

## Prerequisites

- `GROQ_API_KEY` in `.env`
- `INGEST_API_KEY` or `LOCAL_API_CREDENTIAL` set (smoke uses `release-test-key` by default)
- Fixtures: `python scripts/download_dataset_images.py`

## Quick run (all-in-one)

```bash
./scripts/release_test.sh
```

Exits 0 if all pass, 1 if any fail.
Requires `GROQ_API_KEY` for smoke + benchmark.

For CI (no Groq): `RELEASE_USE_MOCK=1 ./scripts/release_test.sh`

## Individual steps

### 1. Integration tests (no Groq)

```bash
PYTHONPATH=. uv run pytest test/test_ingest_integration.py -v
```

Uses mocks where needed. Fast. Verifies ingest paths, auth, agent output structure.

### 2. Production smoke test (real Groq)

```bash
PYTHONPATH=. uv run python scripts/release_smoke_test.py
```

Full pipeline, real Groq, real URLs. Covers:

- Canonical `image_b64`
- Canonical `image_url` (real fetch)
- Frame ingest
- Wyze (X-Attach URL)
- Frigate (image_b64)
- Auth rejection (401)

### 3. Benchmark

```bash
PYTHONPATH=. uv run python scripts/benchmark_pipeline.py --n 5
```

Measures latency (ingest → verdict) per frame. Reports p50, p95, p99.

### 4. Deployment simulation (real-world cases)

```bash
# HTTP mode (running backend)
PYTHONPATH=. uv run python scripts/run_deployment_realworld_tests.py --base-url http://127.0.0.1:8000

# In-process mode (no external server)
PYTHONPATH=. uv run python scripts/run_deployment_realworld_tests.py --inprocess

# In-process + mock agents (no Groq dependency)
PYTHONPATH=. uv run python scripts/run_deployment_realworld_tests.py --inprocess --mock
```

Mode selection:
- Use HTTP mode to validate deployed runtime behavior.
- Use in-process mode for fast local validation and debugging.
- Use in-process + mock for deterministic checks when Groq is unavailable.

Async caveat:
- If ingest runs async, responses may be `status=queued` instead of returning a verdict.
- For direct verdict evaluation, set `INGEST_ASYNC_DEFAULT=false` before running.

How to read confidence and distribution:
- Per case, `confidence` is the model confidence for the selected action.
- `avg_confidence` is only a cohort signal; do not treat it as an accuracy metric.
- `action_distribution=alert:X suppress:Y` shows decision mix; large skew can indicate threshold or dataset bias.
- Read `evaluated_accuracy`, `action_distribution`, and `agent_majority_alignment` together when judging quality.

Mismatch Debug Playbook:
- Confirm `expected_action` vs actual `action`; classify false alert vs missed alert first.
- Compare `expected_reasoning` with model `reasoning`; look for missing threat cues or over-weighted benign context.
- Check agent vote split and majority alignment; close vote disagreements often indicate ambiguous frames.
- Verify async mode in test run; `INGEST_ASYNC_DEFAULT=true` can return `status=queued` instead of verdicts.
- Validate manifest labels against the image/frame used; label drift or stale fixtures can look like model regressions.

## Accuracy-only gate

Run strict action-accuracy validation only:

```bash
./scripts/run_accuracy_gate.sh
```

Outputs:
- action accuracy, alert precision/recall/F1
- benign false-alert rate
- threat missed-alert rate
- repeat-run flip rate
- machine-readable report at `test/reports/accuracy_report.json`

Gate behavior:
- exits non-zero when sample-size gates fail
- exits non-zero when accuracy thresholds fail

## Release checklist

- [ ] `release_test.sh` passes
- [ ] `.env` has `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `INGEST_API_KEY`
- [ ] Fixtures exist (`test/fixtures/images/`)
- [ ] Pilot guardrails verified in `docs/PILOT_ROLLOUT_GUARDRAILS.md`
- [ ] Pilot go/no-go checklist completed in `docs/PILOT_GO_NO_GO_CHECKLIST.md`
