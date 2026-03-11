# Novin Home: Architecture, Prompts & Wiring Breakdown

Complete breakdown of the pipeline, all prompts, how components communicate, and where things are wired.

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           INGEST LAYER                                            │
│  Canonical API / Webhook / StreamPipeline → process_frame()                      │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           FRAME PROCESSING                                       │
│  vision.analyse_frame()  ──┐                                                     │
│  history.query_history()  ─┼──► FramePacket (vision + history + stream_meta)      │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           REASONING PIPELINE (arbiter.run_reasoning)              │
│                                                                                   │
│   Phase 1 (parallel):  Agent 1 ──┐                                                │
│   Agents 1–3 see only            Agent 2 ──┼──► peer_outputs dict                │
│   vision + history                Agent 3 ──┘                                    │
│                                                                                   │
│   Phase 2:           Agent 4 (sees vision + history + PRIOR AGENTS)              │
│                                                                                   │
│   Output: [out1, out2, out3, out4] → _compute_verdict()                          │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           VERDICT & OUTPUT                                        │
│  case_engine.build_case_state() → Verdict                                         │
│  SecurityEventNarrator → headline, narrative                                      │
│  public_verdict() → API payload (rationale sanitized via hallucination_guard)     │
│  notifier.dispatch() → webhook / Slack / email                                   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Entry Points & Wiring

| Entry Point | File | Flow |
|-------------|------|------|
| **Canonical ingest** | `backend/ingest/processor.py` | `process_canonical()` → `process_frame()` → persist → `public_verdict()` → WebSocket broadcast → `notifier.dispatch()` |
| **Stream pipeline** | `backend/agent/pipeline.py` | `StreamPipeline._consumer_loop()` → `process_frame()` → `on_verdict` callback |
| **API ingest** | `backend/api/novin/ingest.py` | Routes to `process_canonical()` |
| **Hub** | `backend/hub.py` | `PipelineManager` owns streams, starts `StreamPipeline` per stream |

---

## 3. Data Flow: Frame → Packet → Verdict

### 3.1 Vision (`backend/agent/vision.py`)

- **Input:** Base64 JPEG frame, `StreamMeta` (camera label, zone, site_id)
- **Output:** `VisionResult` (threat, severity, categories, identity_labels, risk_labels, description, bbox, confidence, uncertainty)
- **Providers:** Groq, SiliconFlow, Together (configurable via `VISION_PROVIDER`)
- **Sanitization (hallucination mitigations):**
  - `_sanitize_identity_labels()` → allowlist `ALLOWED_IDENTITY_LABELS`, prohibited terms → `"person"`
  - `_sanitize_risk_labels()` → allowlist `ALLOWED_RISK_LABELS`, unknown → `"clear"` or `"suspicious_presence"`
  - `_sanitize_description()` → max 150 chars, strips identity terms

### 3.2 History (`backend/agent/history.py`)

- **Input:** DB session, stream_id, site_id, event_types
- **Output:** `HistoryContext` (recent_events, similar_events, camera_baseline, anomaly_score, memory_items)
- **Used by:** All reasoning agents via `_history_summary(packet)`

### 3.3 FramePacket

- **Schema:** `backend/models/schemas.py`
- **Fields:** frame_id, stream_id, timestamp, b64_frame, stream_meta, vision, history, event_context
- **Passed to:** `run_reasoning(packet, ...)` and all agents

---

## 4. Reasoning Agents: Prompts & Inputs

All agents inherit from `ReasoningAgent` in `backend/agent/reasoning/base.py`.

### 4.1 Shared Guardrail Prompt (appended to every agent)

```
_SHARED_GUARDRAIL_PROMPT in base.py:
- Residential home-security reasoning for Novin Home
- Policy version: launch-accuracy-v1
- Prioritize homeowner safety, entry-point risk, tamper, forced entry
- Never infer identity/resident/guest from appearance alone
- Output risk_level: none|low|medium|high
- Rationale format: SIGNAL / EVIDENCE / UNCERTAINTY / DECISION
- Do not claim tools, APIs, or capabilities beyond producing JSON
- verdict="uncertain" only when evidence is genuinely ambiguous
```

### 4.2 Agent 1: Context & Baseline Reasoner

- **File:** `backend/agent/reasoning/context_baseline_reasoner.py`
- **Role:** Evaluate if event makes contextual sense from time, location, historical frequency
- **Lane:** Spatial/temporal only. No intent, trajectory, or psychology.
- **Input:** `_stream_summary` + `_vision_summary` + `_history_summary` + `_preference_summary` + `_memory_summary` + TIME
- **Output JSON:** verdict, risk_level, confidence, rationale, recommended_action, chain_notes (focus, threat_outcome, zone_risk)
- **Peer outputs:** None (Phase 1)

### 4.3 Agent 2: Trajectory & Intent Assessor

- **File:** `backend/agent/reasoning/trajectory_intent_assessor.py`
- **Role:** Deduce psychological intent from movement physics
- **Lane:** Trajectory, dwell, gaze. No time-of-day or baselines.
- **Input:** Same summaries as Agent 1
- **Output JSON:** verdict, risk_level, confidence, rationale, recommended_action, chain_notes (focus, threat_outcome, intent)
- **Peer outputs:** None (Phase 1)

### 4.4 Agent 3: Falsification Auditor (Skeptic)

- **File:** `backend/agent/reasoning/falsification_auditor.py`
- **Role:** Invent most plausible BENIGN explanation; stress-test it
- **Lane:** Red team. If cannot falsify threat → validate it.
- **Input:** Same summaries as Agent 1 (no peer outputs; blind)
- **Output JSON:** verdict, risk_level, confidence, rationale, recommended_action, chain_notes (focus, threat_outcome, benign_theory)
- **Peer outputs:** None (Phase 1)

### 4.5 Agent 4: Executive Triage Commander

- **File:** `backend/agent/reasoning/executive_triage_commander.py`
- **Role:** Final arbiter. Conflict resolution + business logic. No new analysis.
- **Input:** Same summaries + **PRIOR AGENTS** block from `_cognitive_chain_summary(peer_outputs)`
- **Peer outputs:** `{agent1: out1, agent2: out2, agent3: out3}` — verdict, risk_level, confidence, rationale, recommended_action
- **Output JSON:** verdict, risk_level, confidence, rationale, recommended_action, chain_notes (focus, threat_outcome, triage)

### 4.6 Summary Helpers (base.py)

| Helper | Content |
|--------|---------|
| `_vision_summary(packet)` | `V: threat=0/1 sev=... conf=... cats=... risk=... desc='...'` |
| `_history_summary(packet)` | `H: recent=N similar=N last_alert=Xm anomaly=... baseline=...` |
| `_stream_summary(packet)` | `CTX: camera='...' zone='...' site='...' trust=...` |
| `_memory_summary(packet)` | `MEM: scope|type=...|last=...|seen=...|hits=...` |
| `_preference_summary(packet)` | `PREF: key=value ...` |
| `_cognitive_chain_summary(peer_outputs)` | `[agent_id] verdict=... risk_level=... Rationale: ... Action: ...` |

---

## 5. Vision Prompt (Full)

**File:** `backend/agent/vision.py` — `_SYSTEM_PROMPT`

- Home security vision AI for residential camera feeds
- Output **only** valid JSON
- Schema: identity_labels, risk_labels, uncertainty, threat, severity, categories, description, bbox, confidence
- Identity: Cannot recognise faces. Never claim resident, guest, family, neighbor, homeowner, known person. Classify humans as `person`
- threat=true only for credible home security (intrusion, suspicious person, forced entry)
- identity_labels and risk_labels never empty; use `["clear"]` if nothing notable

---

## 6. Arbiter: Verdict Computation

**File:** `backend/agent/reasoning/arbiter.py` — `_compute_verdict()`

### 6.1 Weighted Vote

| Agent | Weight |
|-------|--------|
| context_baseline_reasoner | 0.15 |
| trajectory_intent_assessor | 0.20 |
| falsification_auditor | 0.15 |
| executive_triage_commander | 0.50 |

- `alert_score` = sum of weights for agents with verdict=alert
- `suppress_score` = sum for verdict=suppress
- `uncertain_score` = sum for verdict=uncertain
- `alert_confidence` = alert_score / (alert_score + suppress_score + uncertain_score)
- `suppress_confidence` = suppress_score / same denominator

### 6.2 Adaptive Thresholds (from DB)

- `vote_confidence_threshold` (default 0.55)
- `strong_vote_threshold` (default 0.70)
- `min_alert_confidence` (default 0.35)
- Adapted from home feedback (FP/FN rates) via `compute_home_thresholds()`

### 6.3 Decision Logic (Simplified)

- **has_threat_semantic:** vision risk_labels ∩ HARD_THREAT_RISK_LABELS
- **home_security_signal:** vision risk_labels ∩ HOME_SECURITY_RISK_HINTS
- **entry_zone:** stream_meta.zone in ENTRY_ZONES
- **after_hours:** hour < 6 or hour >= 20
- **fast_path_alert:** threat/history signal + clear_alert_support + limited suppress support + (entry_zone or after_hours)
- **should_alert:** (alert_confidence >= threshold && severity_ok && has_threat_semantic) OR fast_path_alert
- **risk_level:** derived from severity, entry_risk_signal, agent votes
- **Guardrails:** `_apply_launch_guardrails()` — e.g. escalate suppress→alert if explicit threat; suppress alert→suppress if benign-only

### 6.4 Routing Policies (risk_level → action)

| risk_level | action | visibility | notification | storage |
|------------|--------|------------|--------------|---------|
| high | alert | prominent | immediate | full |
| medium | suppress | prominent | review | full |
| low | suppress | timeline | none | timeline |
| none | suppress | hidden | none | diagnostic |

---

## 7. Case Engine & Explainability

**File:** `backend/agent/case_engine.py` — `build_case_state()`

- **Observation:** event_id, stream_id, zone, description, categories, identity_labels, risk_labels, uncertainty, after_hours, anomaly_score
- **Patterns:** threat_patterns, benign_patterns, ambiguity_patterns (from ontology)
- **Case status:** routine, interesting, watch, verify, urgent, active_threat, closed_benign
- **Evidence digest:** timeline context, decision reasoning, agent outputs
- **Consumer summary:** homeowner-facing
- **Operator summary:** operator-facing
- **Perception, Judgement, Routing:** structured contracts for explainability

---

## 8. Output Boundaries & Sanitization

| Boundary | File | Sanitization |
|---------|------|--------------|
| **API payload** | `backend/public.py` | `strip_capability_claims()` on rationale, decision_reason, alert_reason, suppress_reason |
| **Email** | `backend/notifications/notifier.py` | `strip_capability_claims()` on decision_reasoning and agent rationales |
| **Webhook** | Uses `public_verdict()` → already sanitized |
| **Slack** | Uses description, narrative; no rationale in body |

**hallucination_guard.py:** `strip_capability_claims()` removes patterns like "I can ", "I will notify", "I have access", etc. Replaces with `[redacted]`.

---

## 9. Agent Message Bus

**File:** `backend/agent/bus.py`

- **AgentMessageBus:** Created with agent_ids. Used for coordination (e.g. wait_for_all).
- **Publish/get_published:** Agents don't use the bus for peer data in the current flow. Peer outputs are passed directly: `_run_agent(agent4, packet, peer, client)` where `peer = {agent1: out1, agent2: out2, agent3: out3}`.
- **Wiring:** `run_reasoning()` runs agents via `asyncio.gather` and builds `peer` dict manually; bus is used for orchestration/timeouts.

---

## 10. Model Providers

| Component | Config | Providers |
|-----------|-------|-----------|
| **Vision** | `VISION_PROVIDER` | groq, siliconflow, together |
| **Reasoning** | `REASONING_PROVIDER` | groq, cerebras, together, siliconflow |

**Provider module:** `backend/provider.py` — `active_vision_model()`, `active_reasoning_model()`, client getters.

---

## 11. Chain Notes Allowlist

**File:** `backend/agent/reasoning/base.py` — `ALLOWED_CHAIN_NOTE_KEYS`

- Keys: `focus`, `threat_outcome`, `zone_risk`, `intent`, `benign_theory`, `triage`, `recommended_action`, `risk_level`
- In `_validate_output()`: unknown keys dropped, logged

---

## 12. Telemetry

**Verdict.telemetry** includes:

- policy_version, prompt_version
- vision_latency_ms, history_latency_ms, reasoning_latency_ms, pipeline_latency_ms
- reasoning_agent_calls, reasoning_repairs, reasoning_skipped_agents
- reasoning_rounds, phase1/phase2 latency
- case_status, ambiguity_state
- **hallucination_markers** (from `detect_hallucination_markers()` on rationale + description + decision_reasoning)
