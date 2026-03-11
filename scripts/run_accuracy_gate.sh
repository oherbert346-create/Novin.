#!/bin/bash
set -euo pipefail

PYTHONPATH=. uv run python test/test_api_accuracy.py \
  --base-url "${BASE_URL:-http://127.0.0.1:8000}" \
  --api-key "${INGEST_API_KEY:-test-ingest-key}" \
  --manifest "${DEPLOYMENT_BENCHMARK_MANIFEST:-test/fixtures/eval/home_security/home_security_validation_manifest.json}" \
  --repeats "${DEPLOYMENT_BENCHMARK_REPEATS:-2}" \
  --report-json "${DEPLOYMENT_BENCHMARK_REPORT_JSON:-test/reports/deployment_benchmark_report.json}" \
  --min-total-cases "${DEPLOYMENT_MIN_TOTAL_CASES:-12}" \
  --min-benign-cases "${DEPLOYMENT_MIN_BENIGN_CASES:-3}" \
  --min-threat-cases "${DEPLOYMENT_MIN_THREAT_CASES:-2}" \
  --min-ambiguous-cases "${DEPLOYMENT_MIN_AMBIGUOUS_CASES:-2}" \
  --min-temporal-cases "${DEPLOYMENT_MIN_TEMPORAL_CASES:-1}" \
  --min-context-cases "${DEPLOYMENT_MIN_CONTEXT_CASES:-2}" \
  --min-ops-cases "${DEPLOYMENT_MIN_OPS_CASES:-2}" \
  --min-action-accuracy "${DEPLOYMENT_MIN_ACTION_ACCURACY:-0.70}" \
  --min-alert-recall "${DEPLOYMENT_MIN_ALERT_RECALL:-0.50}" \
  --max-false-alert-rate-benign "${DEPLOYMENT_MAX_FALSE_ALERT_RATE_BENIGN:-0.35}" \
  --max-missed-alert-rate-threat "${DEPLOYMENT_MAX_MISSED_ALERT_RATE_THREAT:-0.50}" \
  --max-reasoning-degraded-rate "${DEPLOYMENT_MAX_REASONING_DEGRADED_RATE:-0.10}" \
  --max-fallback-agent-rate "${DEPLOYMENT_MAX_FALLBACK_AGENT_RATE:-0.05}" \
  --max-http-error-rate "${DEPLOYMENT_MAX_HTTP_ERROR_RATE:-0.00}" \
  --min-context-propagation-rate "${DEPLOYMENT_MIN_CONTEXT_PROPAGATION_RATE:-0.95}" \
  --min-idempotency-correctness-rate "${DEPLOYMENT_MIN_IDEMPOTENCY_CORRECTNESS_RATE:-1.00}" \
  --max-flip-rate "${DEPLOYMENT_MAX_FLIP_RATE:-0.20}" \
  --max-p95-latency-s "${DEPLOYMENT_MAX_P95_LATENCY_S:-2.5}" \
  --max-memory-accuracy-regression "${DEPLOYMENT_MAX_MEMORY_ACCURACY_REGRESSION:-0.0}" \
  --max-memory-false-alert-regression "${DEPLOYMENT_MAX_MEMORY_FALSE_ALERT_REGRESSION:-0.0}" \
  --max-memory-missed-alert-regression "${DEPLOYMENT_MAX_MEMORY_MISSED_ALERT_REGRESSION:-0.0}" \
  --max-memory-reasoning-degraded-regression "${DEPLOYMENT_MAX_MEMORY_REASONING_DEGRADED_REGRESSION:-0.0}" \
  --max-memory-flip-regression "${DEPLOYMENT_MAX_MEMORY_FLIP_REGRESSION:-0.0}"
