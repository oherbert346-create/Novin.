# Shadow-First Rollout Readiness

This document turns the shadow-first rollout plan into concrete runtime controls and qualification commands.

## Target Runtime

- `VISION_PROVIDER=siliconflow`
- `REASONING_PROVIDER=cerebras`
- `INGEST_ASYNC_DEFAULT=false`
- `SHADOW_MODE=true`

Shadow mode suppresses external homeowner-facing delivery. If `SHADOW_WEBHOOK_URL` is set, alert payloads are mirrored to that internal sink only.

## Qualification Commands

1. Deploy and verify readiness:

```bash
./scripts/deploy.sh
curl http://127.0.0.1:8000/health/ready
```

2. Run smoke and rollout qualification:

```bash
python3 scripts/run_shadow_qualification.py \
  --base-url http://127.0.0.1:8000 \
  --api-key "$INGEST_API_KEY"
```

3. Inspect generated evidence:

- `test/reports/shadow_qualification_report.json`
- `test/reports/shadow_accuracy_report.json`
- `test/reports/shadow_guardrail_report.json`

## Decision Rules

- Shadow rollout is allowed only when:
  - deploy, readiness, smoke, deployment real-world, accuracy, and guardrail commands all pass
  - contradiction rate is `<= 5%`
  - no obvious persistence/auth/runtime regressions appear
- Small pilot remains blocked until:
  - `pilot_readiness_verdict` becomes pilot-ready from staged real benchmark coverage
  - contradiction rate is `<= 3%`
  - explanation quality is `>= 90%`
  - explainability completeness is `100%`
