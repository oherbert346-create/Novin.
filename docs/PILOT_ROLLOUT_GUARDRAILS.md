# Pilot Rollout Guardrails

## Required Configuration

- `GROQ_API_KEY` must be set.
- `CEREBRAS_API_KEY` must be set when `REASONING_PROVIDER=cerebras`.
- One API credential must be set:
  - `INGEST_API_KEY`, or
  - `LOCAL_API_CREDENTIAL`.
- Use `INGEST_ASYNC_DEFAULT=false` for deterministic pilot evaluation runs.

## API Key Setup

Generate a key:

```bash
python3 scripts/generate_api_key.py --env-name INGEST_API_KEY
```

Write key to `.env`:

```bash
INGEST_API_KEY=novin_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Validate auth behavior:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST http://127.0.0.1:8000/api/novin/ingest \
  -H "Content-Type: application/json" \
  -d '{"cam_id":"cam1","home_id":"home","zone":"front_door","image_url":"http://images.cocodataset.org/val2017/000000000139.jpg"}'
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST http://127.0.0.1:8000/api/novin/ingest \
  -H "x-api-key: $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"cam_id":"cam1","home_id":"home","zone":"front_door","image_url":"http://images.cocodataset.org/val2017/000000000139.jpg"}'
```

Expected:
- without key → `401`
- with key → `200` or queued response depending on ingest mode

## Pilot Go/No-Go Gates

All gates must pass before pilot rollout:

1. Deployment preflight
   - `./scripts/deploy.sh` passes environment checks.
2. Readiness
   - `GET /health/ready` returns `200`.
3. Accuracy baseline
   - `python3 test/test_api_accuracy.py --base-url http://127.0.0.1:8000 --api-key "$INGEST_API_KEY" --manifest test/fixtures/eval/prod_api_accuracy_manifest.json`
   - no failed cases.
4. Explainability contract and quality threshold
   - response includes `routing`, `summary`, `audit.liability_digest`, `audit.agent_outputs`.
   - explanation quality pass rate is `>= 90%` using the pilot explanation rubric in `docs/PILOT_GO_NO_GO_CHECKLIST.md`.
5. Contradiction guardrail threshold
   - contradiction rate is `<= 3%` across evaluated synchronous verdicts.
   - contradiction rate `> 5%` is automatic no-go.
6. Suppress policy
   - suppress events are persisted.
   - suppress details hidden by default in accuracy output.
7. Rollback readiness
   - rollback command path from deploy workflow is documented and tested once in non-prod.
8. Go/no-go checklist complete
   - `docs/PILOT_GO_NO_GO_CHECKLIST.md` is fully checked with evidence links for each gate.

## Pilot Pass/Fail Thresholds

Use these thresholds for pilot sign-off on the vision + reasoning guardrails rollout:

| Metric | Pass | Warning | Fail (No-Go) |
|--------|------|---------|--------------|
| Contradiction rate | `<= 3%` | `> 3%` and `<= 5%` | `> 5%` |
| Explanation quality score | `>= 90%` | `>= 80%` and `< 90%` | `< 80%` |
| Explainability fields completeness | `100%` | N/A | `< 100%` |

Contradiction rate counts verdicts that include `warn:` entries in `CONSISTENCY_CHECKS`.
Explanation quality score is the percentage of sampled verdicts that include clear evidence, explicit uncertainty, and policy-basis language.

## Suppress Handling Policy

- Store all suppress events and traces in database/audit.
- Do not surface suppress details in default test operator output.
- Enable details only in explicit debug mode (`--show-suppress-details`).

## Suppress Visibility Notes

| Surface | Default visibility | Debug visibility |
|---------|--------------------|------------------|
| Operator summary/headline | Show decision and high-level rationale only | Same as default |
| Accuracy output (console/report) | Hide suppress-only rationale details | Show when `--show-suppress-details` is enabled |
| Audit trail (`audit.agent_outputs`, `decision_reasoning`) | Persist full detail | Persist full detail |

Suppress detail suppression is a display policy only. Storage and audit retention remain complete for incident review.

## API Key Rotation

Planned rotation:
1. Generate new key with `scripts/generate_api_key.py`.
2. Add new key to runtime environment.
3. Restart service.
4. Validate ingest using new key.
5. Remove old key from environments and secret stores.

Emergency revoke:
1. Replace compromised key in environment immediately.
2. Restart service.
3. Validate old key now fails with `401`.
4. Re-run smoke + accuracy checks.

## Pilot Monitoring Minimums

- Watch `5xx` error rate during canary.
- Watch ingest latency trend.
- Watch action mix (`alert` vs `suppress`) for sudden drift.
- Review false-alert samples daily during pilot week.
