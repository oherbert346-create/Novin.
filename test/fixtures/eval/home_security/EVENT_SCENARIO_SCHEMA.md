# Event Scenario Schema

The event scenario catalog is the primary input for large synthetic and multi-camera benchmarks.

Top-level fields:

- `schema_version`
- `variant_axes`
- `scenarios`

Each scenario defines:

- `scenario_id`
- `suite`
- `scenario_family`
- `source_mode`
- `home_id`
- `cameras`
- `assets`
- `timeline`
- `correlation_expectations`
- `memory_expectations`
- `benchmark_eligibility`

Important expectations:

- `source_mode` must be one of `nvr_webhook`, `cloud_alert`, or `stream_sampled`.
- `timeline` is event-ordered and becomes flat benchmark cases during expansion.
- `correlation_expectations` defines incident-level scoring:
  - `incident_id_expected`
  - `linked_event_ids_expected`
  - `cross_cam_correlation_expected`
  - `escalation_expected`
  - `final_incident_action_expected`
- `memory_expectations` defines whether memory is expected to help and which preference tags matter.
- `variant_axes` expands each scenario combinatorially into a large flat manifest.
