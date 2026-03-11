from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.policy import UNKNOWN_ZONE

RiskLevel = Literal["none", "low", "medium", "high"]
VisibilityPolicy = Literal["hidden", "timeline", "prominent"]
NotificationPolicy = Literal["none", "review", "immediate"]
StoragePolicy = Literal["diagnostic", "timeline", "full"]
CaseStatus = Literal["routine", "interesting", "watch", "verify", "urgent", "active_threat", "closed_benign"]
AmbiguityState = Literal["resolved", "monitoring", "ambiguous", "contested"]
ConfidenceBand = Literal["low", "medium", "high"]
EvidenceStatus = Literal["supporting", "counter", "missing"]
UncertaintyState = Literal["low", "medium", "high"]
AutonomyEligibility = Literal["not_eligible", "human_confirmation", "low_risk_later"]
ActionType = Literal["notify", "escalate_monitoring", "request_verification", "create_incident", "trigger_scene", "device_signal"]
ActionTargetType = Literal[
    "webhook",
    "operator_queue",
    "homeowner_app",
    "monitoring",
    "smart_home_adapter",
    "timeline",
    "slack",
    "email",
]


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


_LEGACY_VISION_CATEGORIES = {"person", "pet", "package", "vehicle", "intrusion", "motion", "clear"}
_RISK_THREAT_VALUES = {"intrusion", "forced_entry", "suspicious_person", "threat", "high_risk", "critical_risk"}
_VISION_SETTINGS = {"porch_door", "driveway", "yard", "indoor", "street", "garage", "unknown"}
_VISION_ACTIONS = {
    "approaching_entry",
    "standing_at_entry",
    "touching_entry_surface",
    "passing_through",
    "carrying_package",
    "holding_object",
    "loading_or_unloading",
    "standing",
    "moving",
    "stationary",
    "environmental_motion",
    "unclear_action",
}
_VISION_SPATIAL_TAGS = {
    "at_entry",
    "near_entry",
    "at_driveway",
    "near_vehicle",
    "near_fence",
    "inside_threshold",
    "on_walkway",
    "unknown_location",
}
_VISION_OBJECT_LABELS = {
    "package",
    "tool_like_object",
    "phone",
    "bag",
    "unknown_object",
    "none",
}
_VISION_VISIBILITY_TAGS = {
    "clear_view",
    "low_light",
    "blur",
    "partial_subject",
    "occluded",
    "distant_subject",
    "cropped_subject",
    "weather_noise",
}


def _as_string_list(value: object) -> list[str]:
    if isinstance(value, str):
        item = value.strip().lower()
        return [item] if item else []
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item).strip().lower()
        if text:
            items.append(text)
    return items


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalise_vocab_list(value: object, allowed: set[str]) -> list[str]:
    items = _as_string_list(value)
    normalized: list[str] = []
    for item in items:
        if item in allowed and item not in normalized:
            normalized.append(item)
    return normalized


class VisionResult(BaseModel):
    scene_status: Literal["active", "noise"] = "active"
    setting: str = "unknown"
    observed_entities: list[str] = Field(default_factory=list)
    observed_actions: list[str] = Field(default_factory=list)
    spatial_tags: list[str] = Field(default_factory=list)
    object_labels: list[str] = Field(default_factory=list)
    visibility_tags: list[str] = Field(default_factory=list)
    evidence_notes: list[str] = Field(default_factory=list)
    threat: bool = False
    severity: Literal["none", "low", "medium", "high", "critical"] = "none"
    categories: list[Literal["person", "pet", "package", "vehicle", "intrusion", "motion", "clear"]] = Field(
        default_factory=lambda: ["clear"]
    )
    identity_labels: list[str] = Field(default_factory=list)
    risk_labels: list[str] = Field(default_factory=list)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str
    bbox: list[BoundingBox] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    latency_ms: float = 0.0
    usage: dict[str, int] = Field(default_factory=dict)  # prompt_tokens, completion_tokens, total_tokens

    @model_validator(mode="after")
    def _apply_semantic_bridge(self) -> "VisionResult":
        self.setting = self.setting if self.setting in _VISION_SETTINGS else "unknown"
        self.observed_entities = _as_string_list(self.observed_entities)
        self.observed_actions = _normalise_vocab_list(self.observed_actions, _VISION_ACTIONS)
        self.spatial_tags = _normalise_vocab_list(self.spatial_tags, _VISION_SPATIAL_TAGS)
        self.object_labels = _normalise_vocab_list(self.object_labels, _VISION_OBJECT_LABELS)
        self.visibility_tags = _normalise_vocab_list(self.visibility_tags, _VISION_VISIBILITY_TAGS)
        self.evidence_notes = [str(item).strip()[:80] for item in self.evidence_notes if str(item).strip()][:4]

        identity_labels = _as_string_list(self.identity_labels)
        if not identity_labels:
            identity_labels = [entity for entity in self.observed_entities if entity != "clear"]
        if not identity_labels:
            identity_labels = [cat for cat in self.categories if cat != "intrusion"]
            if not identity_labels:
                identity_labels = ["clear"]
        self.identity_labels = identity_labels
        if not self.observed_entities:
            self.observed_entities = list(identity_labels)

        risk_labels = _as_string_list(self.risk_labels)
        if not risk_labels:
            risk_labels = [cat for cat in self.categories if cat in {"intrusion", "motion", "clear"}]
            if self.threat and "intrusion" not in risk_labels:
                risk_labels.append("intrusion")
            if not risk_labels:
                risk_labels = ["clear"]
        self.risk_labels = risk_labels

        if not self.categories:
            categories: list[str] = []
            for label in self.observed_entities + identity_labels + risk_labels + self.object_labels:
                if label in _LEGACY_VISION_CATEGORIES and label not in categories:
                    categories.append(label)
            if not categories:
                categories = ["intrusion"] if self.threat else ["clear"]
            elif self.threat and "intrusion" not in categories and "motion" not in categories:
                categories.append("intrusion")
            self.categories = categories

        if not self.observed_actions:
            if "entry_approach" in risk_labels:
                self.observed_actions.append("approaching_entry")
            elif "entry_dwell" in risk_labels:
                self.observed_actions.append("standing_at_entry")
            elif "tamper" in risk_labels or "forced_entry" in risk_labels:
                self.observed_actions.append("touching_entry_surface")
            elif self.scene_status == "noise":
                self.observed_actions.append("environmental_motion")
            elif self.categories == ["clear"]:
                self.observed_actions.append("unclear_action")
            else:
                self.observed_actions.append("moving")

        if not self.spatial_tags:
            if any(label in risk_labels for label in {"entry_approach", "entry_dwell", "tamper", "forced_entry"}):
                self.spatial_tags.append("at_entry")
            elif "delivery_pattern" in risk_labels:
                self.spatial_tags.append("near_entry")
            else:
                self.spatial_tags.append("unknown_location")

        if not self.object_labels:
            if "package" in self.categories or "package" in self.observed_entities:
                self.object_labels.append("package")
            else:
                self.object_labels.append("none")

        if not self.visibility_tags:
            if self.scene_status == "noise":
                self.visibility_tags.append("weather_noise")
            elif self.uncertainty >= 0.65:
                self.visibility_tags.append("partial_subject")
            else:
                self.visibility_tags.append("clear_view")

        if not self.threat:
            self.threat = any(label in _RISK_THREAT_VALUES for label in risk_labels)

        if self.severity == "none" and self.threat:
            self.severity = "medium"
        if not self.threat and self.severity != "none":
            self.severity = "none"

        uncertainty = float(self.uncertainty)
        if uncertainty == 0.0 and self.confidence < 1.0:
            uncertainty = _clamp01(1.0 - float(self.confidence))
        self.uncertainty = _clamp01(uncertainty)
        if not self.evidence_notes:
            note_parts = []
            if self.setting != "unknown":
                note_parts.append(self.setting.replace("_", " "))
            if self.observed_actions:
                note_parts.append(self.observed_actions[0].replace("_", " "))
            if self.spatial_tags:
                note_parts.append(self.spatial_tags[0].replace("_", " "))
            if note_parts:
                self.evidence_notes = [", ".join(note_parts)[:80]]
        return self


class RecentEvent(BaseModel):
    event_id: str
    stream_id: str
    timestamp: datetime
    severity: str
    categories: list[str]
    description: str
    source: str | None = None
    source_event_id: str | None = None
    event_context: dict = Field(default_factory=dict)


class MemoryItem(BaseModel):
    scope_type: str
    scope_id: str
    memory_key: str
    summary: str
    details: dict = Field(default_factory=dict)
    last_event_id: str | None = None
    hit_count: int = 1


class HistoryContext(BaseModel):
    recent_events: list[RecentEvent] = Field(default_factory=list)
    similar_events: list[RecentEvent] = Field(default_factory=list)
    camera_baseline: dict = Field(default_factory=dict)
    site_baseline: dict = Field(default_factory=dict)
    anomaly_score: float = 0.0
    memory_items: list[MemoryItem] = Field(default_factory=list)


class StreamMeta(BaseModel):
    stream_id: str
    label: str
    site_id: str
    zone: str
    uri: str


class EventContext(BaseModel):
    source: str = "stream"
    source_event_id: str | None = None
    cam_id: str | None = None
    home_id: str | None = None
    zone: str | None = None
    label: str | None = None
    ingest_mode: str = "stream"
    frame_index: int | None = None
    sampled: bool | None = None
    sample_rate: int | None = None
    metadata: dict = Field(default_factory=dict)


class FramePacket(BaseModel):
    frame_id: str
    stream_id: str
    timestamp: datetime
    b64_frame: str
    stream_meta: StreamMeta
    vision: VisionResult
    history: HistoryContext
    event_context: EventContext | None = None


class AgentOutput(BaseModel):
    agent_id: str
    role: str
    verdict: Literal["alert", "suppress", "uncertain"]
    risk_level: RiskLevel = "none"
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    recommended_action: str = ""
    chain_notes: dict = Field(default_factory=dict)
    consumer_headline: str = ""
    consumer_reason: str = ""
    operator_observed: str = ""
    operator_triage: str = ""


class Observation(BaseModel):
    event_id: str = ""
    stream_id: str = ""
    observed_at: datetime | None = None
    zone: str = ""
    source: str = "stream"
    description: str = ""
    categories: list[str] = Field(default_factory=list)
    identity_labels: list[str] = Field(default_factory=list)
    risk_labels: list[str] = Field(default_factory=list)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    after_hours: bool = False
    anomaly_score: float = 0.0
    recent_activity_count: int = 0


class EvidenceItem(BaseModel):
    kind: str
    claim: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str
    status: EvidenceStatus = "supporting"


class PerceptionEvidence(BaseModel):
    categories: list[str] = Field(default_factory=list)
    risk_cues: list[str] = Field(default_factory=list)
    uncertainty_state: UncertaintyState = "low"
    observed_evidence: list[EvidenceItem] = Field(default_factory=list)
    prohibited_inference_compliant: bool = True
    upstream_identity_metadata: dict = Field(default_factory=dict)


class ExplanationContract(BaseModel):
    observed_evidence: list[EvidenceItem] = Field(default_factory=list)
    benign_evidence: list[EvidenceItem] = Field(default_factory=list)
    threat_evidence: list[EvidenceItem] = Field(default_factory=list)
    uncertainty_evidence: list[EvidenceItem] = Field(default_factory=list)
    routing_basis: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class JudgementDecision(BaseModel):
    action: Literal["alert", "suppress"]
    risk_level: RiskLevel = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty_state: UncertaintyState = "low"
    evidence: ExplanationContract = Field(default_factory=ExplanationContract)
    decision_rationale: str = ""
    contradiction_markers: list[str] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    visibility_policy: VisibilityPolicy = "hidden"
    notification_policy: NotificationPolicy = "none"
    storage_policy: StoragePolicy = "diagnostic"
    delivery_targets: list[str] = Field(default_factory=list)
    operator_summary: str = ""
    homeowner_summary: str = ""
    routing_basis: list[str] = Field(default_factory=list)


class ActionIntent(BaseModel):
    action_type: ActionType
    target_type: ActionTargetType
    target: str
    summary: str = ""
    dry_run: bool = True


class ActionReadiness(BaseModel):
    autonomy_eligible: AutonomyEligibility = "not_eligible"
    allowed_action_types: list[ActionType] = Field(default_factory=list)
    required_confirmations: list[str] = Field(default_factory=list)
    tool_targets: list[str] = Field(default_factory=list)
    action_intents: list[ActionIntent] = Field(default_factory=list)


class ConsumerSummary(BaseModel):
    headline: str = ""
    reason: str = ""
    action_now: str = ""


class OperatorSurfaceSummary(BaseModel):
    what_observed: str = ""
    why_flagged: str = ""
    why_not_benign: str = ""
    what_is_uncertain: str = ""
    timeline_context: str = ""
    recommended_next_step: str = ""


class CaseState(BaseModel):
    case_id: str = ""
    case_status: CaseStatus = "routine"
    ambiguity_state: AmbiguityState = "resolved"
    confidence_band: ConfidenceBand = "low"
    observation: Observation = Field(default_factory=Observation)
    evidence_digest: list[EvidenceItem] = Field(default_factory=list)
    consumer_summary: ConsumerSummary = Field(default_factory=ConsumerSummary)
    operator_summary: OperatorSurfaceSummary = Field(default_factory=OperatorSurfaceSummary)
    recommended_next_action: str = ""
    recommended_delivery_targets: list[str] = Field(default_factory=list)
    threat_patterns: list[str] = Field(default_factory=list)
    benign_patterns: list[str] = Field(default_factory=list)
    ambiguity_patterns: list[str] = Field(default_factory=list)
    perception: PerceptionEvidence = Field(default_factory=PerceptionEvidence)
    judgement: JudgementDecision = Field(default_factory=lambda: JudgementDecision(action="suppress"))
    routing_decision: RoutingDecision = Field(default_factory=RoutingDecision)
    action_readiness: ActionReadiness = Field(default_factory=ActionReadiness)


class MachineRouting(BaseModel):
    is_threat: bool
    action: Literal["alert", "suppress"]
    risk_level: RiskLevel = "none"
    severity: str = "none"
    categories: list[str]
    visibility_policy: VisibilityPolicy = "hidden"
    notification_policy: NotificationPolicy = "none"
    storage_policy: StoragePolicy = "diagnostic"

    @model_validator(mode="after")
    def _sync_risk_and_policies(self) -> "MachineRouting":
        severity = (self.severity or "none").lower()
        if self.risk_level == "none":
            if self.action == "alert":
                self.risk_level = "high"
            elif severity in {"critical", "high"}:
                self.risk_level = "high"
            elif severity == "medium":
                self.risk_level = "medium"
            elif severity == "low":
                self.risk_level = "low"
        if self.severity == "none" and self.risk_level != "none":
            self.severity = self.risk_level
        if self.risk_level == "high":
            if self.visibility_policy == "hidden":
                self.visibility_policy = "prominent"
            if self.notification_policy == "none":
                self.notification_policy = "immediate"
            if self.storage_policy == "diagnostic":
                self.storage_policy = "full"
        elif self.risk_level == "medium":
            if self.visibility_policy == "hidden":
                self.visibility_policy = "prominent"
            if self.notification_policy == "none":
                self.notification_policy = "review"
            if self.storage_policy == "diagnostic":
                self.storage_policy = "full"
        elif self.risk_level == "low":
            if self.visibility_policy == "hidden":
                self.visibility_policy = "timeline"
            if self.storage_policy == "diagnostic":
                self.storage_policy = "timeline"
        return self

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
    event_id: str  # Unique event ID for memory/history; same as frame_id for now
    stream_id: str  # cam_id: camera identity
    site_id: str = "home"  # home_id: home identity
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
    event_context: EventContext | None = None
    case: CaseState = Field(default_factory=CaseState)
    case_id: str = ""
    case_status: CaseStatus = "routine"
    ambiguity_state: AmbiguityState = "resolved"
    confidence_band: ConfidenceBand = "low"
    consumer_summary: ConsumerSummary = Field(default_factory=ConsumerSummary)
    operator_summary: OperatorSurfaceSummary = Field(default_factory=OperatorSurfaceSummary)
    evidence_digest: list[EvidenceItem] = Field(default_factory=list)
    recommended_next_action: str = ""
    recommended_delivery_targets: list[str] = Field(default_factory=list)
    perception: PerceptionEvidence = Field(default_factory=PerceptionEvidence)
    judgement: JudgementDecision = Field(default_factory=lambda: JudgementDecision(action="suppress"))
    routing_decision: RoutingDecision = Field(default_factory=RoutingDecision)
    action_readiness: ActionReadiness = Field(default_factory=ActionReadiness)
    telemetry: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sync_case_view(self) -> "Verdict":
        def _sync_literal(case_value: str, top_level_value: str, default: str) -> tuple[str, str]:
            if case_value != default or top_level_value == default:
                return case_value, case_value
            return top_level_value, top_level_value

        if self.case.case_id and not self.case_id:
            self.case_id = self.case.case_id
        elif self.case_id and not self.case.case_id:
            self.case.case_id = self.case_id

        self.case.case_status, self.case_status = _sync_literal(self.case.case_status, self.case_status, "routine")
        self.case.ambiguity_state, self.ambiguity_state = _sync_literal(
            self.case.ambiguity_state,
            self.ambiguity_state,
            "resolved",
        )
        self.case.confidence_band, self.confidence_band = _sync_literal(
            self.case.confidence_band,
            self.confidence_band,
            "low",
        )

        if self.case.consumer_summary and not any(
            [self.consumer_summary.headline, self.consumer_summary.reason, self.consumer_summary.action_now]
        ):
            self.consumer_summary = self.case.consumer_summary
        else:
            self.case.consumer_summary = self.consumer_summary

        if self.case.operator_summary and not any(
            [
                self.operator_summary.what_observed,
                self.operator_summary.why_flagged,
                self.operator_summary.why_not_benign,
                self.operator_summary.what_is_uncertain,
                self.operator_summary.timeline_context,
                self.operator_summary.recommended_next_step,
            ]
        ):
            self.operator_summary = self.case.operator_summary
        else:
            self.case.operator_summary = self.operator_summary

        if self.case.evidence_digest and not self.evidence_digest:
            self.evidence_digest = self.case.evidence_digest
        else:
            self.case.evidence_digest = self.evidence_digest

        if self.case.recommended_next_action and not self.recommended_next_action:
            self.recommended_next_action = self.case.recommended_next_action
        else:
            self.case.recommended_next_action = self.recommended_next_action

        if self.case.recommended_delivery_targets and not self.recommended_delivery_targets:
            self.recommended_delivery_targets = self.case.recommended_delivery_targets
        else:
            self.case.recommended_delivery_targets = self.recommended_delivery_targets

        if self.case.perception.observed_evidence and not self.perception.observed_evidence:
            self.perception = self.case.perception
        else:
            self.case.perception = self.perception

        if self.case.judgement.decision_rationale and not self.judgement.decision_rationale:
            self.judgement = self.case.judgement
        else:
            self.case.judgement = self.judgement

        if self.case.routing_decision.delivery_targets and not self.routing_decision.delivery_targets:
            self.routing_decision = self.case.routing_decision
        else:
            self.case.routing_decision = self.routing_decision

        if self.case.action_readiness.tool_targets and not self.action_readiness.tool_targets:
            self.action_readiness = self.case.action_readiness
        else:
            self.case.action_readiness = self.action_readiness
        return self


class StreamCreate(BaseModel):
    uri: str
    label: str
    site_id: str = "home"
    zone: str = UNKNOWN_ZONE  # front_door, porch, driveway, backyard, garage, living_room, kitchen, unknown


class StreamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    uri: str
    label: str
    site_id: str
    zone: str
    created_at: datetime
    active: bool


class EventResponse(BaseModel):
    id: str
    stream_id: str
    timestamp: datetime
    risk_level: RiskLevel
    severity: str
    categories: list[str]
    description: str
    bbox: list[dict]
    b64_thumbnail: str
    verdict_action: str
    visibility_policy: VisibilityPolicy = "hidden"
    notification_policy: NotificationPolicy = "none"
    storage_policy: StoragePolicy = "diagnostic"
    decision_reason: str | None = None
    agent_traces: list[dict] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
