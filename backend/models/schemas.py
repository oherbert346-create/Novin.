from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class VisionResult(BaseModel):
    threat: bool
    severity: Literal["none", "low", "medium", "high", "critical"]
    categories: list[Literal["intrusion", "crowd", "object", "behaviour", "clear"]]
    description: str
    bbox: list[BoundingBox] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    latency_ms: float = 0.0


class RecentEvent(BaseModel):
    event_id: str
    stream_id: str
    timestamp: datetime
    severity: str
    categories: list[str]
    description: str


class HistoryContext(BaseModel):
    recent_events: list[RecentEvent] = Field(default_factory=list)
    similar_events: list[RecentEvent] = Field(default_factory=list)
    camera_baseline: dict = Field(default_factory=dict)
    site_baseline: dict = Field(default_factory=dict)
    anomaly_score: float = 0.0


class StreamMeta(BaseModel):
    stream_id: str
    label: str
    site_id: str
    zone: str
    uri: str


class FramePacket(BaseModel):
    frame_id: str
    stream_id: str
    timestamp: datetime
    b64_frame: str
    stream_meta: StreamMeta
    vision: VisionResult
    history: HistoryContext


class AgentOutput(BaseModel):
    agent_id: str
    role: str
    verdict: Literal["alert", "suppress", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    chain_notes: dict = Field(default_factory=dict)


class MachineRouting(BaseModel):
    is_threat: bool
    action: Literal["alert", "suppress"]
    severity: str
    categories: list[str]

class OperatorSummary(BaseModel):
    headline: str
    narrative: str

class LiabilityDigest(BaseModel):
    decision_reasoning: str
    confidence_score: float

class AuditTrail(BaseModel):
    liability_digest: LiabilityDigest
    agent_outputs: list[AgentOutput] = Field(default_factory=list)

class Verdict(BaseModel):
    frame_id: str
    stream_id: str
    timestamp: datetime
    
    # Tier 1
    routing: MachineRouting
    
    # Tier 2
    summary: OperatorSummary
    
    # Tier 3
    audit: AuditTrail
    
    # Extras
    description: str
    bbox: list[BoundingBox] = Field(default_factory=list)
    b64_thumbnail: str = ""


class StreamCreate(BaseModel):
    uri: str
    label: str
    site_id: str = "default"
    zone: str = "general"


class StreamResponse(BaseModel):
    id: str
    uri: str
    label: str
    site_id: str
    zone: str
    created_at: datetime
    active: bool

    class Config:
        from_attributes = True


class EventResponse(BaseModel):
    id: str
    stream_id: str
    timestamp: datetime
    severity: str
    categories: list[str]
    description: str
    bbox: list[dict]
    b64_thumbnail: str
    verdict_action: str
    final_confidence: float
    agent_traces: list[dict] = Field(default_factory=list)

    class Config:
        from_attributes = True
