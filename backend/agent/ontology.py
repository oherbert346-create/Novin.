from __future__ import annotations

THREAT_PATTERNS = {
    "forced_entry",
    "entry_dwell",
    "perimeter_progression",
    "tamper",
    "loitering",
    "stalking_repeat_presence",
    "suspicious_vehicle_behavior",
    "dangerous_wildlife",
    "occupancy_anomaly",
    "interior_breach",
}

BENIGN_PATTERNS = {
    "resident_routine",
    "package_delivery",
    "neighbor_pass_through",
    "pet_activity",
    "expected_visitor",
    "routine_vehicle",
    "environmental_motion",
}

AMBIGUITY_PATTERNS = {
    "poor_visibility",
    "partial_subject",
    "isolated_motion",
    "occlusion",
    "conflicting_evidence",
    "missing_historical_context",
}


def sort_patterns(patterns: set[str], allowed: set[str]) -> list[str]:
    return sorted(pattern for pattern in patterns if pattern in allowed)
