# Temporal Correlation & Schedule Learning Implementation Plan

## Overview

This plan outlines implementation of two key intelligence features:

1. **Temporal Correlation** - Link related events into sequences (delivery, intrusion, routine)
2. **Schedule Learning** - Learn household patterns to reduce false alarms

**Excluded**: Identity learning (as requested)

---

## Part 1: Temporal Correlation

### Concept

Detect event sequences that indicate behavior patterns:

```
Sequence Types:
- DELIVERY: motion → package appears → person leaves
- INTRUSION: perimeter breach → movement inside → escalation  
- RESIDENT: front_door → hallway → kitchen (known pattern)
- LOITERING: same camera, multiple events, prolonged presence
```

### Architecture

```
Event Processing Pipeline:
1. Process current event → get verdict
2. Query recent events (same camera, last N minutes)
3. Classify sequence pattern
4. Adjust verdict based on pattern
5. Store sequence links
```

### Implementation Steps

#### Step 1.1: Add Sequence Fields to Event Model

```python
# backend/models/db.py additions:
class Event(Base):
    # ... existing fields ...
    
    # Sequence linking
    sequence_id: str = Column(String, nullable=True)  # Links events in sequence
    sequence_position: int = Column(Integer, nullable=True)  # 1, 2, 3...
    sequence_type: str = Column(String, nullable=True)  # "delivery", "intrusion", "routine"
    
    # Event linking (parent event)
    parent_event_id: str = Column(String, ForeignKey("events.id"), nullable=True)
```

#### Step 1.2: Create Sequence Detector Service

```python
# backend/agent/sequence.py

class SequenceDetector:
    """Detects event sequences and patterns."""
    
    # Time windows for sequence detection
    SEQUENCE_WINDOW_MINUTES = 15
    RESIDENT_PATTERN_WINDOW_HOURS = 2
    
    async def analyze_sequence(
        self, 
        current_event: Event, 
        recent_events: list[Event]
    ) -> SequenceAnalysis:
        """Analyze if current event is part of a known pattern."""
        
        if not recent_events:
            return SequenceAnalysis(
                is_sequenced=False,
                sequence_type=None,
                confidence=0.0,
                adjusted_action=None
            )
        
        # Classify the sequence
        sequence_type = self._classify_sequence(recent_events)
        
        # Determine if we should adjust verdict
        adjusted = self._adjust_for_sequence(
            current_event, 
            recent_events, 
            sequence_type
        )
        
        return adjusted
    
    def _classify_sequence(self, events: list[Event]) -> str | None:
        """Classify the sequence pattern."""
        
        categories = [json.loads(e.categories) for e in events]
        
        # Delivery pattern: motion → package
        if self._is_delivery_pattern(categories):
            return "delivery"
        
        # Intrusion pattern: perimeter → interior
        if self._is_intrusion_pattern(events):
            return "intrusion"
        
        # Resident pattern: known path (front_door → hallway → kitchen)
        if self._is_resident_pattern(events):
            return "resident"
        
        # Loitering: same camera, multiple events
        if self._is_loitering(events):
            return "loitering"
        
        return None
    
    def _is_delivery_pattern(self, categories: list[list[str]]) -> bool:
        """Check for delivery: person → package, or package appears."""
        
        has_person = any("person" in cats for cats in categories)
        has_package = any("package" in cats for cats in categories)
        
        # Direct: person with package
        if has_person and has_package:
            return True
        
        return has_person and len(categories) >= 2
    
    def _is_intrusion_pattern(self, events: list[Event]) -> bool:
        """Check for intrusion: perimeter → interior zones."""
        
        perimeter_zones = {"front_door", "backyard", "driveway", "porch"}
        interior_zones = {"living_room", "kitchen", "bedroom", "garage"}
        
        zones = [e.stream_id for e in events]
        
        has_perimeter = any(z in perimeter_zones for z in zones)
        has_interior = any(z in interior_zones for z in zones)
        
        if has_perimeter and has_interior:
            first_interior_idx = next(
                (i for i, z in enumerate(zones) if z in interior_zones),
                None
            )
            last_perimeter_idx = next(
                (len(zones) - 1 - i for i, z in enumerate(reversed(zones)) if z in perimeter_zones),
                None
            )
            if first_interior_idx and last_perimeter_idx:
                return first_interior_idx > last_perimeter_idx
        
        return False
    
    def _is_loitering(self, events: list[Event]) -> bool:
        """Check for loitering: same camera, 3+ events in window."""
        
        if len(events) < 3:
            return False
        
        cameras = set(e.stream_id for e in events)
        if len(cameras) != 1:
            return False
        
        timestamps = sorted(e.timestamp for e in events)
        time_span = (timestamps[-1] - timestamps[0]).total_seconds()
        
        return 300 <= time_span <= 1800  # 5-30 minutes
    
    def _adjust_for_sequence(
        self, 
        current: Event, 
        sequence: list[Event],
        sequence_type: str | None
    ) -> SequenceAnalysis:
        """Adjust verdict based on sequence pattern."""
        
        if not sequence_type:
            return SequenceAnalysis(
                is_sequenced=False,
                sequence_type=None,
                confidence=0.0,
                adjusted_action=None
            )
        
        adjustments = {
            "delivery": {
                "suppress_confidence": 0.8,
                "reason": "Package delivery sequence detected"
            },
            "resident": {
                "suppress_confidence": 0.9,
                "reason": "Matches known resident movement pattern"
            },
            "loitering": {
                "alert_confidence": 0.3,
                "reason": "Prolonged presence detected"
            },
            "intrusion": {
                "alert_confidence": 0.5,
                "reason": "Interior movement after perimeter breach"
            }
        }
        
        return SequenceAnalysis(
            is_sequenced=True,
            sequence_type=sequence_type,
            confidence=adjustments[sequence_type].get("suppress_confidence", 0.0) or 
                      adjustments[sequence_type].get("alert_confidence", 0.0),
            adjusted_action=adjustments[sequence_type]
        )
```

---

## Part 2: Schedule Learning

### Concept

Learn household patterns to distinguish expected vs unexpected events:

```
Learned Patterns:
- Kids arrive home at 3pm weekdays
- Delivery typically 10am-2pm
- Night (10pm-6am) should be quiet
- Weekend mornings: higher activity OK
```

### Architecture

```
Schedule Learning:
1. Collect event timestamps over time
2. Cluster by hour/day to find patterns
3. Store as HomeSchedule model
4. Use in reasoning to adjust confidence
```

### Implementation Steps

#### Step 2.1: Add Schedule Model

```python
# backend/models/db.py

class HomeSchedule(Base):
    """Learned schedule for a home."""
    
    __tablename__ = "home_schedules"
    
    id = Column(String, primary_key=True, default=_uuid)
    site_id = Column(String, nullable=False, index=True)
    
    typical_arrivals = Column(Text, default="{}")
    typical_departures = Column(Text, default="{}")
    expected_visitors = Column(Text, default="{}")
    
    quiet_hours_start = Column(Integer, nullable=True)
    quiet_hours_end = Column(Integer, nullable=True)
    
    events_analyzed = Column(Integer, default=0)
    last_updated = Column(DateTime, default=_now)
```

#### Step 2.2: Create Schedule Learner Service

```python
# backend/agent/schedule.py

class ScheduleLearner:
    """Learns household patterns from event history."""
    
    MIN_EVENTS_FOR_LEARNING = 50
    QUIET_HOUR_THRESHOLD = 5
    PEAK_HOUR_THRESHOLD = 30
    
    async def learn_schedule(self, db: AsyncSession, site_id: str) -> HomeSchedule:
        """Analyze event history and build schedule."""
        
        events = await self._get_all_events(db, site_id)
        
        if len(events) < self.MIN_EVENTS_FOR_LEARNING:
            return None
        
        hourly_dist = self._compute_hourly_distribution(events)
        quiet_hours = self._find_quiet_hours(hourly_dist)
        
        schedule = HomeSchedule(
            site_id=site_id,
            typical_arrivals=json.dumps(hourly_dist),
            quiet_hours_start=quiet_hours[0] if quiet_hours else None,
            quiet_hours_end=quiet_hours[1] if quiet_hours else None,
            events_analyzed=len(events)
        )
        
        return schedule
    
    def _compute_hourly_distribution(self, events: list[Event]) -> dict[int, float]:
        hourly = {h: 0 for h in range(24)}
        for e in events:
            hour = e.timestamp.hour
            hourly[hour] += 1
        
        total = len(events)
        return {h: (count / total * 100) for h, count in hourly.items()}
    
    def _find_quiet_hours(self, hourly_dist: dict[int, float]) -> tuple[int, int] | None:
        quiet = [h for h, pct in hourly_dist.items() 
                 if pct < self.QUIET_HOUR_THRESHOLD]
        
        if not quiet:
            return None
        
        quiet.sort()
        return (min(quiet), max(quiet))
    
    async def get_schedule_adjustment(
        self, 
        db: AsyncSession, 
        site_id: str, 
        event_timestamp: datetime
    ) -> ScheduleAdjustment:
        """Get confidence adjustment based on learned schedule."""
        
        schedule = await self._get_schedule(db, site_id)
        
        if not schedule:
            return ScheduleAdjustment(
                adjustment=0.0,
                is_expected=False,
                reason="No schedule learned yet"
            )
        
        hour = event_timestamp.hour
        
        if schedule.quiet_hours_start and schedule.quiet_hours_end:
            is_quiet_hour = self._in_range(
                hour, 
                schedule.quiet_hours_start, 
                schedule.quiet_hours_end
            )
            if is_quiet_hour:
                return ScheduleAdjustment(
                    adjustment=0.15,
                    is_expected=False,
                    reason=f"Event during learned quiet hours ({schedule.quiet_hours_start}-{schedule.quiet_hours_end})"
                )
        
        typical_arrivals = json.loads(schedule.typical_arrivals)
        hour_activity = typical_arrivals.get(str(hour), 0)
        
        if hour_activity > self.PEAK_HOUR_THRESHOLD:
            return ScheduleAdjustment(
                adjustment=-0.2,
                is_expected=True,
                reason=f"Event during typical high-activity hour ({hour}:00)"
            )
        
        return ScheduleAdjustment(
            adjustment=0.0,
            is_expected=True,
            reason="Event within normal parameters"
        )
    
    def _in_range(self, hour: int, start: int, end: int) -> bool:
        if start <= end:
            return start <= hour <= end
        return hour >= start or hour <= end
```

---

## Part 3: Integration

### Files to Modify

| File | Changes |
|------|---------|
| `backend/models/db.py` | Add Event.sequence_*, HomeSchedule model |
| `backend/agent/sequence.py` | NEW - SequenceDetector |
| `backend/agent/schedule.py` | NEW - ScheduleLearner |
| `backend/agent/pipeline.py` | Integrate sequence + schedule |
| `backend/agent/reasoning/arbiter.py` | Apply adjustments to verdict |

---

## Implementation Order

1. **Database changes** - Add sequence fields + HomeSchedule
2. **SequenceDetector** - Core detection logic
3. **Pipeline integration** - Hook up sequence analysis
4. **ScheduleLearner** - Basic hourly distribution
5. **Schedule integration** - Hook up to reasoning
6. **Testing** - Unit tests for both components
