from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship, remote, backref

from backend.policy import UNKNOWN_ZONE


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class Stream(Base):
    __tablename__ = "streams"

    id = Column(String, primary_key=True, default=_uuid)
    uri = Column(String, nullable=False)
    label = Column(String, nullable=False)
    site_id = Column(String, nullable=False, default="home")
    zone = Column(String, nullable=False, default=UNKNOWN_ZONE)
    created_at = Column(DateTime, default=_now)
    active = Column(Boolean, default=False)

    events = relationship("Event", back_populates="stream", lazy="dynamic")


class Event(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True, default=_uuid)
    stream_id = Column(String, ForeignKey("streams.id"), nullable=False)
    zone = Column(String, nullable=True)
    timestamp = Column(DateTime, default=_now, nullable=False)
    severity = Column(String, nullable=False, default="none")
    categories = Column(Text, nullable=False, default="[]")
    description = Column(Text, nullable=False, default="")
    bbox = Column(Text, nullable=False, default="[]")
    b64_thumbnail = Column(Text, nullable=False, default="")
    verdict_action = Column(String, nullable=False, default="suppress")
    final_confidence = Column(Float, nullable=False, default=0.0)
    summary = Column(Text, nullable=False, default="")
    narrative_summary = Column(Text, nullable=False, default="")
    alert_reason = Column(Text, nullable=True)
    suppress_reason = Column(Text, nullable=True)
    source_event_id = Column(String, nullable=True)
    source = Column(String, nullable=True)
    event_context = Column(Text, nullable=False, default="{}")
    
    # Sequence linking for temporal correlation
    sequence_id = Column(String, nullable=True, index=True)
    sequence_position = Column(Integer, nullable=True)
    sequence_type = Column(String, nullable=True)  # "delivery", "intrusion", "resident", "loitering"
    parent_event_id = Column(String, ForeignKey("events.id"), nullable=True)
    
    # User feedback and identity tagging
    user_tag = Column(String, nullable=True)  # "resident", "guest", "vendor", "unknown"
    user_feedback = Column(String, nullable=True)  # "false_positive", "false_negative", "none"
    user_feedback_timestamp = Column(DateTime, nullable=True)

    stream = relationship("Stream", back_populates="events")
    agent_traces = relationship("AgentTrace", back_populates="event", cascade="all, delete-orphan")
    linked_events = relationship(
        "Event",
        backref=backref("parent_event", remote_side=[id]),
        remote_side=[parent_event_id],
        foreign_keys=[parent_event_id],
    )

    __table_args__ = (
        Index("ix_events_stream_ts", "stream_id", "timestamp"),
        Index("ix_events_severity_ts", "severity", "timestamp"),
        Index("ix_events_source_dedup", "source", "source_event_id", unique=True),
        Index("ix_events_sequence", "sequence_id", "sequence_position"),
    )


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id = Column(String, primary_key=True, default=_uuid)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    agent_id = Column(String, nullable=False)
    role = Column(String, nullable=False)
    verdict = Column(String, nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)
    rationale = Column(Text, nullable=False, default="")
    chain_notes = Column(Text, nullable=False, default="{}")

    event = relationship("Event", back_populates="agent_traces")


class HomeSchedule(Base):
    """Learned schedule patterns for a home.
    
    Stores learned patterns like typical arrival times, quiet hours,
    and expected visitor patterns to reduce false positives.
    """
    
    __tablename__ = "home_schedules"

    id = Column(String, primary_key=True, default=_uuid)
    site_id = Column(String, nullable=False, index=True)
    
    # Learned patterns as JSON
    typical_arrivals = Column(Text, default="{}")  # {hour: percentage}
    typical_departures = Column(Text, default="{}")
    expected_visitors = Column(Text, default="{}")  # {day: [visitor_type]}
    
    # Quiet hours (learned automatically)
    quiet_hours_start = Column(Integer, nullable=True)  # hour (0-23)
    quiet_hours_end = Column(Integer, nullable=True)
    
    # Learning stats
    events_analyzed = Column(Integer, default=0)
    last_updated = Column(DateTime, default=_now, onupdate=_now)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("ix_home_schedules_site", "site_id", unique=True),
    )


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id = Column(String, primary_key=True, default=_uuid)
    scope_type = Column(String, nullable=False, index=True)
    scope_id = Column(String, nullable=False, index=True)
    memory_key = Column(String, nullable=False)
    summary = Column(Text, nullable=False, default="")
    details = Column(Text, nullable=False, default="{}")
    last_event_id = Column(String, nullable=True)
    hit_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_agent_memories_scope_key", "scope_type", "scope_id", "memory_key", unique=True),
    )


class HomeThresholdConfig(Base):
    """Per-home adaptive confidence threshold configuration.
    
    Stores learned confidence thresholds for each home based on
    user feedback (false positives and false negatives) to minimize
    false positives while maintaining security coverage.
    """
    
    __tablename__ = "home_threshold_configs"

    id = Column(String, primary_key=True, default=_uuid)
    site_id = Column(String, nullable=False, index=True)
    
    # Adaptive thresholds (learned from user feedback)
    vote_confidence_threshold = Column(Float, nullable=False, default=0.55)
    strong_vote_threshold = Column(Float, nullable=False, default=0.70)
    min_alert_confidence = Column(Float, nullable=False, default=0.35)
    
    # Feedback counters (30-day rolling window)
    fp_count_30d = Column(Integer, nullable=False, default=0)
    fn_count_30d = Column(Integer, nullable=False, default=0)
    total_alerts_30d = Column(Integer, nullable=False, default=0)
    
    # Threshold tuning metadata
    last_tuned = Column(DateTime, nullable=True)
    tuning_reason = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_home_threshold_configs_site", "site_id", unique=True),
    )
