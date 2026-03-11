#!/bin/bash
# Release test — run before deploy. Exits 1 if any step fails.
# set RELEASE_USE_MOCK=1 to skip real Groq (e.g. CI)
set -e
cd "$(dirname "$0")/.."
echo "=== Release test ==="

# 1. Unit/integration tests (mocks OK)
echo ""
echo "1. pytest (integration tests)..."
PYTHONPATH=. uv run pytest test/test_ingest_integration.py -v --tb=short -q

# 2. Production smoke
echo ""
if [ -n "$RELEASE_USE_MOCK" ]; then
  echo "2. Smoke test (mock, no Groq)..."
  PYTHONPATH=. uv run python scripts/release_smoke_test.py --mock
else
  echo "2. Smoke test (full pipeline, real Groq)..."
  PYTHONPATH=. uv run python scripts/release_smoke_test.py
fi

# 3. Benchmark (optional, 3 frames; skip if mock)
echo ""
if [ -n "$RELEASE_USE_MOCK" ]; then
  echo "3. Benchmark skipped (RELEASE_USE_MOCK)"
else
  echo "3. Benchmark (3 frames)..."
  PYTHONPATH=. uv run python scripts/benchmark_pipeline.py --n 3
fi

echo ""
echo "=== Release test PASSED ==="
