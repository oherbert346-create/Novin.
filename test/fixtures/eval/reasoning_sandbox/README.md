# Text-to-Text Reasoning Sandbox

No video required. Test reasoning agents using LLM-generated synthetic scenarios.

## Workflow

1. **Generate scenarios** (uses LLM to create diverse home-security cases):

   ```bash
   python scripts/generate_synthetic_scenarios.py --count 1000 --output test/fixtures/eval/reasoning_sandbox/synthetic_scenarios.jsonl
   ```

   For faster generation with more parallel API calls:

   ```bash
   python scripts/generate_synthetic_scenarios.py --count 1000 --concurrency 20
   ```

2. **Run reasoning sandbox** (runs 4 agents on each scenario, no vision API):

   ```bash
   python scripts/run_reasoning_sandbox.py --scenarios test/fixtures/eval/reasoning_sandbox/synthetic_scenarios.jsonl
   ```

   Quick smoke test with sample scenarios:

   ```bash
   python scripts/run_reasoning_sandbox.py --scenarios test/fixtures/eval/reasoning_sandbox/sample_scenarios.jsonl --limit 10
   ```

3. **Output** – metrics (action accuracy, cohort accuracy, latency) and optional JSON report.

## Environment

- `CEREBRAS_API_KEY`, `SILICONFLOW_API_KEY`, `TOGETHER_API_KEY`, or `GROQ_API_KEY` for reasoning
- Same provider as `REASONING_PROVIDER` in config

## GPU

For faster scenario generation, use a machine with GPU or increase `--concurrency` to saturate API rate limits.
