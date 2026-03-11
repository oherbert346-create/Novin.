# Intelligence Gaps & Unicorn Features

## Current State Analysis

### What Works Well ✅

1. **Multi-agent reasoning pipeline** - 4 agents with weighted voting
2. **Adversarial challenger** - Explicitly reduces false positives
3. **Zone-based context** - Entry points elevate risk
4. **After-hours detection** - Night time increases suspicion
5. **Idempotent processing** - No duplicate events

### Critical Gaps ❌

## Gap 1: No Identity System

**Problem**: Every person is treated as potentially unknown

**Current behavior**:
```
Vision: person detected
ThreatEscalation: unknown person → alert
BehaviouralPattern: evaluate intent → uncertain
AdversarialChallenger: could be resident → suppress?
Final: Depends on threshold
```

**What's missing**:
- No way to mark someone as "known resident"
- No learning from repeated occurrences
- No facial recognition (by design, but no fallback)

**Solution for unicorn**:
```python
# Add to Event model:
user_tag: str = "unknown" | "known_resident" | "delivery_driver" | "trusted_visitor"

# Add feedback endpoint:
POST /api/events/{id}/tag
{ "tag": "known_resident", "notes": "It's the neighbor John" }

# Reasoning gets context:
if event.user_tag == "known_resident":
    # Suppress with high confidence
```

---

## Gap 2: No Temporal Correlation

**Problem**: Events are processed independently

**Current behavior**:
```
Event A: person at front door (10:00)
Event B: person at back door (10:02)  # Independent!
Event C: package delivered (10:05)    # No connection
```

**What's missing**:
- No sequence detection (person → package → leaves = delivery)
- No cross-event patterns (same person, different camera)
- No trajectory analysis (entering → moving inside = resident)

**Solution for unicorn**:
```python
# Event linking in database
linked_event_id: str = ForeignKey("events.id")
sequence_position: int  # 1, 2, 3 in sequence
sequence_type: str = "delivery" | "intrusion" | "routine"

# Add to reasoning context:
"Temporal: person at front_door 2min ago, package now present"
"Trajectory: front_door → hallway → kitchen (resident pattern)"
```

---

## Gap 3: Weak Anomaly Detection

**Problem**: Simple z-score doesn't adapt to environment

**Current behavior**:
```python
# history.py
anomaly_score = (current_rate - baseline) / baseline
# Problem: A busy driveway (50 events/day) looks like anomaly
```

**What's missing**:
- Environment type learning (suburban vs urban)
- Seasonal patterns (more visitors during holidays)
- Weather correlation (rain = less motion = same alert threshold)

**Solution for unicorn**:
```python
class EnvironmentProfile:
    camera_id: str
    baseline_events_per_hour: float
    typical_hours: list[int]  # [8, 9, 17, 18] = commute times
    weather_correlation: dict  # {rain: -0.3, snow: -0.5}
    
# Dynamic threshold:
if environment.profile == "busy_street":
    base_threshold = 0.85  # Higher bar for alert
else:
    base_threshold = 0.70
```

---

## Gap 4: No "Expected Activity" Model

**Problem**: System doesn't know household patterns

**Current behavior**:
- Front door motion at 2am → potential alert
- Front door motion at 2pm → could be anything

**What's missing**:
- Learned schedule (kids come home at 3pm)
- Recurring patterns (delivery every Tuesday)
- Exception handling (vacation mode)

**Solution for unicorn**:
```python
class HomeSchedule:
    # Learned from history
    typical_arrivals: {  # time: [(weekday, frequency)]
        "15:00": [("Friday", 0.9), ("Monday", 0.7)],
        "18:30": [("Weekday", 0.95)],
    }
    expected_visitors: ["Tuesday: package", "Friday: grocery"]

# In reasoning:
if time_matches_schedule(event.time, home.schedule):
    confidence *= 0.5  # Reduce alert confidence
```

---

## Gap 5: Limited Explainability

**Problem**: Narrative is template-based, not grounded

**Current output**:
```
Headline: "Medium-severity person detected in front_door; 
          alert confidence 72%."
Narrative: "• Activity: person (medium severity)...
           • Agent consensus: 2 alert, 1 suppress..."
```

**What's missing**:
- Why this specific decision?
- What would change the verdict?
- What's the evidence?

**Solution for unicorn**:
```python
# Richer explanation
explanation = """
DECISION: SUPPRESS (75% confidence)

REASONING:
• Person detected matches typical delivery window (2-4pm)
• Package visible in frame
• Similar event 3 hours ago was confirmed delivery
• Adversarial challenger found plausible benign explanation

WOULD ALERT IF:
• Person stayed longer than 5 minutes
• Person moved toward door/lock
• No package visible

EVIDENCE:
• Vision: person@x:320-450,y:200-400 (bounding box)
• History: 3 similar events this week, all deliveries
"""
```

---

## Unicorn Feature Priority

| Feature | Complexity | Impact | Priority |
|---------|------------|--------|----------|
| User Feedback Loop | Medium | 🔥🔥🔥 | 1 |
| Event Linking | Medium | 🔥🔥🔥 | 1 |
| Richer Explanations | Low | 🔥🔥 | 2 |
| Environment Profiles | High | 🔥🔥 | 3 |
| Schedule Learning | High | 🔥🔥 | 3 |

---

## Quick Wins

### 1. Add Feedback Endpoint

```python
# backend/api/events.py
@router.post("/events/{event_id}/tag")
async def tag_event(
    event_id: str,
    tag: str = Body(..., embed=True),  # known_resident, false_alarm, etc.
    db: AsyncSession = Depends(get_db)
):
    """Allow users to provide feedback on event classification."""
    # Store in database
    # Invalidate confidence cache
    # If many false_alarm, lower threshold for similar events
```

### 2. Add Sequence Detection

```python
# In process_canonical, after getting verdict:
async def check_sequence(payload, verdict, db):
    # Look for recent events on same camera
    recent = await get_recent_events(payload.cam_id, window_minutes=10)
    
    if recent and verdict.action == "alert":
        # Check if this continues a pattern
        if _is_intrusion_sequence(recent, verdict):
            escalate(verdict)
```

### 3. Richer Agent Context

```python
# Add to base.py prompt injection:
"""
IMPORTANT: Before deciding, consider:
1. Is this consistent with the typical pattern for this time of day?
2. Have similar events been confirmed as benign by user feedback?
3. What additional evidence would change your decision?

If you are uncertain about identity, prefer 'uncertain' verdict.
"""
```

---

## Summary

The system has excellent foundations for a home security AI. To reach "unicorn" status:

1. **Immediate**: Add user feedback loop for identity learning
2. **Short-term**: Add event sequencing for behavior patterns  
3. **Medium-term**: Add environment profiling for adaptive thresholds
4. **Long-term**: Add full schedule learning

The adversarial challenger is already a great start - it explicitly argues for suppression. Building on this with user feedback would be the highest-impact improvement.
