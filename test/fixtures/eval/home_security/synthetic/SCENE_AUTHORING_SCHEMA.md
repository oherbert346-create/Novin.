# Synthetic Scene Authoring Schema

This catalog is for synthetic accuracy authoring that matches the Novin Home pipeline:

1. `scene_truth`
2. `simulated_vision`
3. `expected_reasoning_inputs`
4. `expected_judgement`
5. `expected_routing`
6. `expected_action_readiness`

The key rule is strict:

- `scene_truth` may contain hidden author knowledge.
- `simulated_vision` may contain only what a vision model could infer from the frame itself.
- `expected_judgement` may use `simulated_vision` plus context, history, and upstream metadata.

## Required Fields

- `scenario_id`
- `scenario_family`
- `cohort`
- `zone`
- `time_context`
- `scene_brief`
- `camera_view`
- `scene_truth`
- `simulated_vision`
- `expected_reasoning_inputs`
- `expected_judgement`
- `expected_routing`
- `expected_action_readiness`
- `false_positive_critical`

## `scene_truth`

Hidden author-only truth. This is never treated as direct model output.

Suggested fields:

- `actual_outcome`
- `authorized_presence`
- `intent`
- `actor_role`
- `safety_relevance`

## `simulated_vision`

This must behave like perception only.

Allowed:

- concrete visible description
- visible categories such as `person`, `pet`, `package`, `vehicle`, `motion`, `clear`
- visible objects
- directly observable risk cues such as `entry_approach`, `entry_dwell`, `tamper`
- uncertainty and confidence
- explicit unknowns

Not allowed:

- identity claims such as resident, roommate, neighbor, cleaner, house sitter
- trust claims such as authorized, trusted, familiar
- final intent labels such as theft, maintenance, routine, delivery unless visually explicit enough to be treated as a cue rather than a conclusion
- routing or alert decisions

Required `simulated_vision` fields:

- `description`
- `categories`
- `visible_objects`
- `risk_cues`
- `uncertainty`
- `confidence`
- `known_unknowns`
- `prohibited_inference_compliant`

## `expected_reasoning_inputs`

This is the non-vision context the judgement layer is allowed to use.

Suggested fields:

- `upstream_identity_metadata`
- `time_context_facts`
- `history_context`
- `memory_hints`

## Authoring Rules

- Write the `simulated_vision.description` like a careful witness, not an investigator.
- If the frame does not prove identity, intent, or trust, put that in `known_unknowns`.
- Use `false_positive_critical: true` for scenes where an unnecessary alert would quickly damage product trust.
- Keep autonomy classifications as policy-only readiness labels, never execution instructions.
