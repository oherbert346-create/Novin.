# Home Security Research Brief

This brief captures the launch assumptions behind `launch-accuracy-v1`.

## Residential Security Norms

- Consumer products commonly distinguish `person`, `pet`, `package`, and `vehicle`.
- Users strongly prefer fewer nuisance alerts from deliveries, pets, weather, and routine daytime activity.
- Entry zones and after-hours activity are the primary escalation contexts.

## Prompting Patterns

- Structured outputs need a fixed schema, strict wording, and explicit failure behavior.
- Uncertainty should be first-class rather than forcing overconfident classifications.
- Policy prompts should forbid unsupported inferences and require evidence-grounded rationale.

## Privacy Handling

- Consumer home-security systems should not infer named identity from visual appearance.
- Familiarity may only come from vendor metadata or user-provided feedback, not biometric-style reasoning.

## Implementation Consequence

- The launch system is context-driven, not identity-driven.
- Routine-pattern suppression should come from zone, time, sequence, history, and benign cues.
