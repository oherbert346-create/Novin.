# Synthetic Acceptance Rules

Synthetic assets are secondary evidence only.

Accept a synthetic case only if:

- the camera viewpoint looks like a mounted residential security camera
- the action is visually discernible without narrative explanation
- the zone semantics are clear enough to support the decision label
- the scene looks operational, not cinematic or commercial
- provenance fields are filled:
  - `generated_from_case_id`
  - `generator_type`
  - `prompt_version`
  - `augmentation_type`

Use synthetic variants to expand:

- day and night coverage
- glare, rain, fog, and headlight artifacts
- occlusion and compression
- posture and clothing variation
- rare wildlife or tamper scenarios

Do not use synthetic-only performance to claim pilot readiness.
