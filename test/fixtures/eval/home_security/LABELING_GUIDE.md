# Labeling Guide

Label operational decisions, not generic scene classes.

Allowed decisions:

- `alert`: likely security-relevant activity that should notify a homeowner.
- `suppress`: benign, routine, or low-evidence activity that should not notify.
- `ambiguous`: optional review-only bucket for cases that should not be part of the hard gate.

Required metadata:

- `zone`
- `time_of_day`
- `indoor_outdoor`
- `scenario_type`
- `source_type`
- `quality_grade`
- `benchmark_eligibility`

Reject a case from the pilot gate if:

- the actor or threat cue is not visually discernible
- the camera viewpoint is unrealistic for a home camera
- the frame is too blurred, dark, or compressed to support the label
- the label depends on information not visible in the asset
