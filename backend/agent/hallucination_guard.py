"""Hallucination risk mitigations: capability-strip and marker detection for consumer-facing output."""

from __future__ import annotations

import re

_CAPABILITY_PATTERNS = [
    r"\bI\s+can\s+",
    r"\bI\s+will\s+notify\b",
    r"\bI\s+have\s+access\b",
    r"\bI\s+am\s+able\s+to\b",
    r"\bthe\s+system\s+will\b",
    r"\bI\s+will\s+alert\b",
    r"\bI\s+will\s+send\b",
    r"\bI\s+have\s+the\s+ability\b",
    r"\bI\s+can\s+access\b",
    r"\bI\s+can\s+see\b",
    r"\bI\s+detect\s+",
    r"\bI\s+recognise\b",
    r"\bI\s+recognize\b",
]

_HALLUCINATION_MARKER_PATTERNS = _CAPABILITY_PATTERNS + [
    r"\bknown\s+person\b",
    r"\bfamiliar\s+face\b",
    r"\btrusted\s+visitor\b",
    r"\bresident\b",
    r"\bhomeowner\b",
    r"\bowner\b",
]

_CAPABILITY_RE = re.compile("|".join(f"({p})" for p in _CAPABILITY_PATTERNS), re.IGNORECASE)
_MARKER_RE = re.compile("|".join(f"({p})" for p in _HALLUCINATION_MARKER_PATTERNS), re.IGNORECASE)


def strip_capability_claims(text: str) -> str:
    """Remove capability claims from rationale before consumer-facing output.

    Replaces matched spans with [redacted]. Preserves rationale structure
    (SIGNAL/EVIDENCE/UNCERTAINTY/DECISION). Internal verdict logic uses raw text.
    """
    if not text or not isinstance(text, str):
        return text
    return _CAPABILITY_RE.sub("[redacted]", text)


def detect_hallucination_markers(text: str) -> list[str]:
    """Return list of matched hallucination marker patterns for telemetry.

    No behavior change; used for observability and dashboards.
    """
    if not text or not isinstance(text, str):
        return []
    matches: list[str] = []
    for m in _MARKER_RE.finditer(text):
        span = m.group(0).strip()
        if span and span.lower() not in (x.lower() for x in matches):
            matches.append(span)
    return matches
