from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


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
    site_id = Column(String, nullable=False, default="default")
    zone = Column(String, nullable=False, default="general")
    created_at = Column(DateTime, default=_now)
    active = Column(Boolean, default=False)

    events = relationship("Event", back_populates="stream", lazy="dynamic")


class Event(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True, default=_uuid)
    stream_id = Column(String, ForeignKey("streams.id"), nullable=False)
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

    stream = relationship("Stream", back_populates="events")
    agent_traces = relationship("AgentTrace", back_populates="event", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_events_stream_ts", "stream_id", "timestamp"),
        Index("ix_events_severity_ts", "severity", "timestamp"),
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
