from __future__ import annotations

POLICY_VERSION = "launch-accuracy-v1"
PROMPT_VERSION = "launch-accuracy-v1-qwen1"

BLESSED_STACK = {
    "vision_provider": "siliconflow",
    "vision_model": "Qwen/Qwen2.5-VL-7B-Instruct",
    "reasoning_provider": "groq",
    "reasoning_model": "qwen/qwen3-32b",
}

IDENTITY_METADATA_KEYS = {
    "known_person",
    "familiar_face",
    "trusted_visitor",
    "trusted_person",
}

PROHIBITED_IDENTITY_TERMS = {
    "resident",
    "guest",
    "neighbor",
    "neighbour",
    "family",
    "homeowner",
    "owner",
    "known person",
    "trusted visitor",
    "familiar face",
}

HARD_THREAT_RISK_LABELS = {
    "tamper",
    "forced_entry",
    "entry_dwell",
    "perimeter_progression",
    "suspicious_presence",
    "suspicious_person",
}

HARD_BENIGN_RISK_LABELS = {
    "delivery_pattern",
    "benign_activity",
    "resident_routine",
}

BENIGN_CATEGORIES = {"pet", "package", "vehicle", "clear"}
ENTRY_ZONES = {"front_door", "porch", "garage", "back_door", "backyard", "living_room", "kitchen"}
UNKNOWN_ZONE = "unknown"
HOME_SECURITY_RISK_HINTS = {
    "entry_approach",
    "entry_dwell",
    "tamper",
    "forced_entry",
    "perimeter_progression",
    "suspicious_presence",
    "suspicious_person",
    "wildlife_near_entry",
}

ALLOWED_RISK_LABELS = (
    HOME_SECURITY_RISK_HINTS
    | HARD_THREAT_RISK_LABELS
    | HARD_BENIGN_RISK_LABELS
    | {"clear", "intrusion", "motion"}
)

ALLOWED_IDENTITY_LABELS = {
    "person",
    "pet",
    "package",
    "vehicle",
    "unknown",
    "clear",
    "wildlife",
    "delivery_person",
    "unknown_resident",
}

RELEASE_LATENCY_BUDGET_MS = {
    "pipeline_p95": 3000.0,
    "vision_p95": 1200.0,
    "reasoning_p95": 1200.0,
    "overhead_p95": 600.0,
}
