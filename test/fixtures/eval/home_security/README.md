# Novin Home Validation Ladder

This directory separates pre-pilot evidence into distinct suites.

- `staged_real_home`: primary pilot gate. Only this suite can justify outreach or pilot readiness.
- `synthetic_home`: secondary evidence for coverage expansion and regression detection.
- `public_surveillance`: secondary stress evidence for anomaly and crime robustness.
- `smoke_pipeline`: pipeline sanity only.

Rules:

- Do not count `synthetic_home`, `public_surveillance`, or `smoke_pipeline` toward pilot-readiness claims.
- Mark low-fidelity assets as `benchmark_eligibility: "review_only"` so they stay visible without contaminating the gate.
- Keep all cases in `home_security_validation_manifest.json` on the shared schema so the runner and reports stay stable.

Event-driven benchmark inputs:

- `event_scenario_catalog.json`: main source of truth for large synthetic and multi-cam temporal scenarios.
- `generated_event_manifest.json`: flat case manifest generated from the scenario catalog for the existing runner.
- `home_security_validation_manifest.json`: small hand-curated validation manifest for current smoke and secondary evidence.
- `synthetic/synthetic_vision_authoring_catalog.json`: authored synthetic scenes with simulated vision output and expected judgement/routing/action-readiness contracts.
- `synthetic/synthetic_vision_holdout_catalog.json`: held-out synthetic scenes for post-tuning generalization checks.

Generate the large flat manifest with:

```bash
PYTHONPATH=. uv run python scripts/generate_home_security_benchmark.py
```

Run the holdout catalog with:

```bash
REASONING_PROVIDER=groq PYTHONPATH=. uv run python scripts/compare_reasoning_models_accuracy.py --synthetic \
  --synthetic-catalog test/fixtures/eval/home_security/synthetic/synthetic_vision_holdout_catalog.json \
  --limit-synthetic 15
```
