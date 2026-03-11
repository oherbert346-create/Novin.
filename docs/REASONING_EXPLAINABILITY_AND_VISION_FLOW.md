# Reasoning Explainability, Vision Flow, and Hallucination Guardrails

## 1. Explainability Report Format (Saved Reference)

When running the reasoning sandbox with `--output`, each result includes full explainability:

```json
{
  "scenario_id": "...",
  "cohort": "benign|threat|ambiguous",
  "expected": "alert|suppress",
  "actual": "alert|suppress",
  "risk_level": "none|low|medium|high",
  "confidence": 0.0-1.0,
  "reasoning_latency_ms": 1234.5,
  "explainability": {
    "consumer_summary": {
      "headline": "Check recent activity at porch",
      "reason": "person may need verification because of entry approach.",
      "action_now": "Notify the homeowner, keep the case visible..."
    },
    "operator_summary": {
      "what_observed": "person in porch; categories=person; risk_labels=entry_approach.",
      "why_flagged": "routed as medium risk with 0.30 anomaly signal",
      "why_not_benign": "no dominant benign pattern",
      "what_is_uncertain": "missing_historical_context",
      "timeline_context": "no linked case history yet",
      "recommended_next_step": "Notify the homeowner..."
    },
    "evidence_digest": [
      {"kind": "vision", "claim": "...", "source": "vision", "status": "supporting"},
      {"kind": "consensus", "claim": "0 alert, 3 suppress, decision=suppress", "source": "reasoning_agents", "status": "supporting"}
    ],
    "judgement": {
      "decision_rationale": "RISK_BASIS: ... CONFIDENCE_DECOMPOSITION: ...",
      "contradiction_markers": []
    },
    "decision_reasoning": "Full routing basis and confidence breakdown",
    "agent_outputs": [
      {
        "agent_id": "context_baseline_reasoner",
        "verdict": "suppress",
        "risk_level": "low",
        "rationale": "SIGNAL: ... EVIDENCE: ... UNCERTAINTY: ... DECISION: ...",
        "chain_notes": {"threat_outcome": "LOW", "zone_risk": "medium", ...}
      },
      ...
    ]
  }
}
```

**Usage:** `python scripts/run_reasoning_sandbox.py --scenarios ... --output test/reports/report.json`

**Sample report:** `test/reports/single_event_explainability.json` (run with `--limit 1` to generate)

---

## 2. Vision → Reasoning Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. FRAME INGEST                                                              │
│    Raw image (numpy/PIL) → base64 JPEG                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. VISION LAYER (vision_agent.analyse_frame)                                │
│    - Input: b64 image + stream_meta (camera label, zone, site_id)           │
│    - Model: VL model (Qwen2.5-VL, Llama-Scout, etc.) via Groq/Together/SF   │
│    - Output: VisionResult                                                    │
│      • threat (bool)                                                         │
│      • severity (none|low|medium|high|critical)                              │
│      • categories (person|pet|package|vehicle|intrusion|motion|clear)        │
│      • identity_labels (person, unknown, delivery_person, etc.)             │
│      • risk_labels (entry_approach, entry_dwell, tamper, etc.)              │
│      • description (~30 words)                                               │
│      • confidence, uncertainty                                               │
│      • bbox (normalised 0–1)                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. HISTORY LAYER (history_agent.query_history) — runs in parallel with vision│
│    - Input: db, stream_id, site_id, event_types                              │
│    - Output: HistoryContext (recent_events, similar_events, baseline, etc.)  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. FRAME PACKET                                                              │
│    FramePacket = frame_id, timestamp, b64_frame, stream_meta,               │
│                  vision (VisionResult), history (HistoryContext)            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. REASONING LAYER (run_reasoning)                                           │
│    Agents receive TEXT SUMMARIES only — not raw image:                       │
│                                                                              │
│    _vision_summary(packet) → "V: threat=0 sev=low conf=0.92 cats=person     │
│      risk=entry_approach desc='Mail carrier walks to porch...'"              │
│    _history_summary(packet) → "H: recent=2 similar=1 last_alert=15m ago     │
│      anomaly=0.30 baseline=1.50 top_similar=medium"                          │
│    _stream_summary(packet) → "CTX: camera='Porch' zone='porch' trust=none"   │
│                                                                              │
│    Phase 1 (parallel): Agents 1–3 each get these summaries + raw text        │
│    Phase 2: Agent 4 gets Phase 1 outputs + same summaries                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 6. VERDICT                                                                   │
│    Arbiter weights agent outputs, applies guardrails, builds CaseState       │
│    (consumer_summary, operator_summary, evidence_digest, judgement)           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key point:** Reasoning agents never see the raw image. They only see:
- A short vision summary string (threat, severity, categories, risk_labels, description)
- History summary (recent events, baseline, anomaly)
- Stream context (camera, zone, trust metadata)

---

## 3. Hallucination Risks and Mitigations

### Do the models hallucinate about tools or capabilities?

**Current mitigations:**

1. **No tool-calling / function-calling**  
   The reasoning agents use chat completions only. They output JSON. They have no access to tools, APIs, or external actions. The system does not expose "you can do X" in prompts.

2. **Explicit guardrail (base.py):**
   ```
   - Never fabricate facts not present in provided context.
   ```

3. **Strict output schema**  
   Agents must return only: `verdict`, `risk_level`, `confidence`, `rationale`, `recommended_action`, `chain_notes`. No free-form tool claims.

4. **Lane discipline**  
   "Stay strictly within your assigned cognitive lane. Do not analyze domains assigned to other specialists." Reduces scope creep and invented capabilities.

5. **Identity prohibition**  
   "Never infer identity, resident status, guest status, family membership, or familiarity from appearance alone. A person is only known/trusted if explicit upstream metadata says so." Prevents identity hallucination.

### Remaining risks

- **Evidence hallucination:** An agent could invent details not in the vision summary (e.g. "person was carrying a crowbar" when vision said only "person"). The "Never fabricate facts" guardrail targets this but is not enforceable.
- **Capability hallucination:** An agent could mention "I can notify the operator" or "I have access to X" in rationale. The schema does not include such fields, but free-text rationale could contain it. We do not currently strip or detect this.
- **Vision model:** The vision layer sees the raw image and outputs structured JSON. It can hallucinate objects, labels, or risk cues not present in the image. Vision has strict schema and identity rules to reduce this.

### Recommended additions (if needed)

- Add to shared guardrail: "Do not claim or imply you have tools, APIs, or capabilities beyond producing this JSON. You cannot take actions."
- Post-process rationale for capability claims (e.g. "I can", "I will notify", "I have access") and flag or strip them.
- Log and monitor rationale text for hallucination markers in production.
