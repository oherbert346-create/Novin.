# Pilot Go/No-Go Checklist (Vision + Reasoning Guardrails)

Use this checklist before production pilot rollout for the guardrails spec.

## Quality Gates

- [ ] Run deployment evaluation in HTTP or in-process mode and archive output.
- [ ] Confirm contradiction rate is `<= 3%` across sampled synchronous verdicts.
- [ ] Confirm explanation quality score is `>= 90%` across sampled synchronous verdicts.
- [ ] Confirm explainability field completeness is `100%` for sampled synchronous verdicts.

## Contradiction Rate Gate

Pass/fail rules:
- Pass: `<= 3%`
- Warning: `> 3%` and `<= 5%`
- No-Go: `> 5%`

How to measure:
1. Run deployment real-world tests.
2. Count evaluated synchronous verdicts.
3. Count verdicts where `audit.liability_digest.decision_reasoning` contains `warn:` under `CONSISTENCY_CHECKS`.
4. Compute `contradiction_rate = contradiction_warnings / evaluated_sync_verdicts`.

Evidence to attach:
- Command output showing evaluated verdict count.
- Command output showing contradiction warning count.
- Computed contradiction rate.

## Explanation Quality Gate

Pass/fail rules:
- Pass: `>= 90%`
- Warning: `>= 80%` and `< 90%`
- No-Go: `< 80%`

Scoring rubric per sampled verdict:
- Evidence quality: rationale includes concrete observations, not only labels.
- Uncertainty clarity: rationale states uncertainty or confidence limitations when applicable.
- Policy basis: rationale states why alert criteria were met or not met.

A verdict passes the rubric only if all three criteria are present.

Evidence to attach:
- Sample set size and sampling method.
- Per-sample pass/fail notes for the three criteria.
- Final explanation quality score.

## Suppress Visibility Gate

- [ ] Default operator outputs do not expose suppress-only detail blocks.
- [ ] Debug mode (`--show-suppress-details`) reveals suppress-only detail blocks.
- [ ] Audit persistence still stores full suppress reasoning and agent traces.

Evidence to attach:
- Default output sample.
- Debug output sample.
- Stored verdict sample showing full `audit.agent_outputs`.

## Operational Gates

- [ ] `./scripts/deploy.sh` preflight succeeds.
- [ ] `GET /health/ready` returns `200` and all checks healthy.
- [ ] Accuracy baseline run has zero failed manifest cases.
- [ ] Rollback path from deploy workflow is tested once in non-prod.

## Decision

- [ ] GO
- [ ] NO-GO

Decision owner:
- Name:
- Date:
- Notes:
