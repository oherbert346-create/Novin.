# Test Plan: Novin Home Security Intelligence Validation

## Executive Summary

This plan outlines a comprehensive testing strategy to validate the system's intelligence for distinguishing real security events from false alarms. The goal is to identify gaps and build toward a "unicorn" prototype that achieves high accuracy, explainability, and fast response times.

---

## 1. Current Testing Landscape

### Existing Tests

| Test File | Purpose | Coverage |
|-----------|---------|----------|
| `test_ingest_accuracy.py` | Unit tests for zone inference, schema validation, config | ✅ Unit level |
| `test_ingest_integration.py` | Integration tests for full ingest pipeline | ✅ Integration |
| `test_api_accuracy.py` | Real-image accuracy evaluation with manifest | ✅ End-to-end |
| `test_pipeline.py` | Pipeline functionality tests | ✅ Pipeline |
| `test_pipeline_async.py` | Async pipeline behavior | ✅ Async |

### What's Missing

1. **False Alarm Scenario Tests** - No systematic tests for common false positives
2. **Reasoning Agent Tests** - No tests validating agent decision logic
3. **Temporal/Cross-Camera Tests** - No tests for multi-event correlation
4. **Explainability Tests** - No tests validating narrative generation quality
5. **Latency Tests** - No benchmarks for response time SLA

---

## 2. Intelligence Gaps Identified

### Critical Gaps for "Unicorn" Status

| Gap | Current State | Impact | Priority |
|-----|---------------|--------|----------|
| **No identity learning** | System treats every person as unknown | Can't distinguish regular visitor from intruder | HIGH |
| **No temporal correlation** | Events are independent, no "person followed by package" patterns | Miss coordinated attacks | HIGH |
| **Weak anomaly detection** | Simple z-score, limited context | False positives in high-motion environments | MEDIUM |
| **No "expected activity" model** | System doesn't learn household patterns | Night motion alerts for routine events | MEDIUM |
| **Limited explainability** | Narrative is template-based | Users can't understand AI reasoning | MEDIUM |

---

## 3. Test Scenarios for False Alarm Validation

### Category A: Common False Positives (Should SUPPRESS)

| Scenario | Description | Expected Verdict | Test Method |
|----------|-------------|------------------|-------------|
| **Pet motion** | Cat/dog moving in frame | suppress | Synthetic image + label |
| **Package delivery** | Delivery person at door | suppress | Vision category="package" |
| **Vehicle pass-by** | Car driving on street | suppress | Zone=street, category=vehicle |
| **Shadow/movement** | Tree shadow, light change | suppress | Vision threat=false |
| **Insects** | Bug on camera lens | suppress | Small motion, no object |
| **Rain/snow** | Weather-related motion | suppress | Visual conditions in metadata |
| **Routine resident** | Known resident at normal time | suppress | Time-based + history |

### Category B: Real Security Events (Should ALERT)

| Scenario | Description | Expected Verdict | Test Method |
|----------|-------------|------------------|-------------|
| **Unknown person at night** | Stranger at back door 2am | alert | Time + zone + unknown |
| **Forced entry** | Door open with person | alert | Category=intrusion |
| **Loitering** | Person lingering >30s | alert | Temporal analysis |
| **Multiple camera trigger** | Same person across cameras | alert | Cross-camera correlation |
| **Package theft** | Person takes package | alert | Behavioral sequence |

### Category C: Edge Cases (Should be UNCERTAIN)

| Scenario | Description | Expected Verdict | Test Method |
|----------|-------------|------------------|-------------|
| **Partial view** | Person partially visible | uncertain | Vision confidence <0.6 |
| **Unclear intent** | Person at door, can't see hands | uncertain | Multiple agents disagree |
| **New object** | Unknown object in frame | uncertain | Category="unknown" |

---

## 4. Proposed Test Suite Structure

### New Test Files to Create

```
test/
├── test_false_alarm_scenarios.py    # False positive test cases
├── test_security_event_scenarios.py # Real event test cases  
├── test_reasoning_agents.py         # Agent logic validation
├── test_temporal_correlation.py     # Multi-event patterns
├── test_explainability.py           # Narrative quality tests
├── test_latency_benchmarks.py       # Performance SLA tests
└── fixtures/
    └── scenarios/                   # Synthetic scenario definitions
        ├── false_alarm/
        ├── real_events/
        └── edge_cases/
```

### Test Data Format

```json
{
  "scenario_id": "pet_motion_001",
  "category": "false_alarm",
  "description": "Cat walking in backyard",
  "image_source": "fixtures/scenarios/false_alarm/pet_backyard.jpg",
  "camera_config": {
    "zone": "backyard",
    "time": "14:00",
    "site_id": "test_home"
  },
  "expected": {
    "verdict": "suppress",
    "min_confidence": 0.6,
    "category": "pet",
    "reasoning_contains": ["pet", "animal", "benign"]
  }
}
```

---

## 5. Intelligence Enhancement Tests

### Test: Identity Learning Readiness

```python
def test_system_can_learn_known_residents():
    """
    System should eventually learn recurring patterns.
    Current gap: No mechanism to mark events as 'known resident'
    
    Test validates:
    1. Event can be tagged with user feedback
    2. Feedback is stored for future correlation
    3. Repeated pattern reduces alert frequency
    """
    # Step 1: Fire alert for unknown person
    # Step 2: User marks as "known resident"  
    # Step 3: Fire similar event again
    # Step 4: Verify suppress or higher uncertainty
    pass
```

### Test: Temporal Pattern Detection

```python
def test_delivery_sequence_detection():
    """
    System should detect: motion → package → person leaves
    
    Current gap: No sequence detection
    
    Test validates:
    1. Events within time window are correlated
    2. Behavioral agent receives sequence context
    3. Package theft is distinguishable from delivery
    """
    pass
```

### Test: Anomaly Detection Under Load

```python
def test_high_motion_environment():
    """
    Test system in high-motion scenario (busy street, tree-heavy yard)
    Current: Simple z-score fails in high-baseline scenarios
    
    Test validates:
    1. Alert threshold adapts to environment
    2. False positive rate stays acceptable
    3. Real events still detected
    """
    pass
```

---

## 6. Accuracy Metrics to Track

### Primary Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **False Positive Rate** | < 5% | (False Alerts / Total Events) |
| **True Positive Rate** | > 95% | (Real Alerts / Real Events) |
| **Mean Time to Alert** | < 3s | Timestamp delta |
| **Explainability Score** | > 80% | User can understand reasoning |

### Secondary Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Agent Consensus Rate** | > 70% | All agents agree |
| **Fallback Rate** | < 2% | Agents return uncertain |
| **Latency P95** | < 5s | 95th percentile |

---

## 7. Implementation Roadmap

### Phase 1: Baseline (Week 1)

- [ ] Run existing `test_api_accuracy.py` to establish baseline
- [ ] Create `test_false_alarm_scenarios.py` with 20 common false positive cases
- [ ] Create `test_security_event_scenarios.py` with 20 real event cases
- [ ] Measure initial false positive rate

### Phase 2: Intelligence Validation (Week 2)

- [ ] Create `test_reasoning_agents.py` to validate agent logic
- [ ] Add temporal correlation tests
- [ ] Measure agent consensus and fallback rates

### Phase 3: Production Readiness (Week 3)

- [ ] Create latency benchmarks
- [ ] Add explainability scoring tests
- [ ] Run 1000+ event synthetic load test

---

## 8. Questions to Answer

Before proceeding with implementation, clarify:

1. **Target Environment**: Are you testing with real cameras or synthetic data?
2. **Baseline Accuracy**: What's your current false positive rate if known?
3. **User Feedback Loop**: Do you want to test the feedback mechanism for learning?
4. **Timeline**: When do you need the unicorn-level accuracy?

---

## 9. Quick Wins to Implement Now

Based on current codebase gaps:

| Enhancement | Effort | Impact |
|-------------|--------|--------|
| Add more false alarm scenarios to test manifest | Low | High |
| Create reasoning agent unit tests | Medium | High |
| Add latency tracking to existing tests | Low | Medium |
| Implement "expected time" feature | Medium | High |

---

## Summary

The system has a solid foundation with the 4-agent reasoning pipeline. The main gaps are:

1. **No identity learning** - Can't learn "this is the homeowner"
2. **No temporal patterns** - Events are independent
3. **Limited anomaly detection** - Struggles in high-motion environments

For the "unicorn" prototype, focus on:
- Building feedback loop for user confirmation
- Implementing time-window based correlation
- Adding environmental baseline learning

Should I proceed with implementing the Phase 1 test suite?
