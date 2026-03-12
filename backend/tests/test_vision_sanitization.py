from __future__ import annotations

import unittest

from backend.agent.vision import (
    _sanitize_description,
    _sanitize_evidence_notes,
    _sanitize_identity_labels,
    _sanitize_object_labels,
    _sanitize_observed_actions,
    _sanitize_risk_labels,
    _sanitize_setting,
    _sanitize_spatial_tags,
)
from backend.policy import ALLOWED_IDENTITY_LABELS, PROHIBITED_IDENTITY_TERMS


class SanitizeIdentityLabelsTests(unittest.TestCase):
    """Tests for _sanitize_identity_labels — enforces allowlist and maps prohibited terms."""

    def test_allowed_label_person_passes_through(self):
        self.assertEqual(_sanitize_identity_labels(["person"]), ["person"])

    def test_allowed_label_pet_passes_through(self):
        self.assertEqual(_sanitize_identity_labels(["pet"]), ["pet"])

    def test_allowed_label_vehicle_passes_through(self):
        self.assertEqual(_sanitize_identity_labels(["vehicle"]), ["vehicle"])

    def test_allowed_label_package_passes_through(self):
        self.assertEqual(_sanitize_identity_labels(["package"]), ["package"])

    def test_allowed_label_clear_passes_through(self):
        self.assertEqual(_sanitize_identity_labels(["clear"]), ["clear"])

    def test_allowed_label_wildlife_passes_through(self):
        self.assertEqual(_sanitize_identity_labels(["wildlife"]), ["wildlife"])

    def test_allowed_label_delivery_person_passes_through(self):
        self.assertEqual(_sanitize_identity_labels(["delivery_person"]), ["delivery_person"])

    def test_allowed_label_unknown_resident_passes_through(self):
        self.assertEqual(_sanitize_identity_labels(["unknown_resident"]), ["unknown_resident"])

    def test_prohibited_resident_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["resident"]), ["person"])

    def test_prohibited_guest_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["guest"]), ["person"])

    def test_prohibited_neighbor_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["neighbor"]), ["person"])

    def test_prohibited_neighbour_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["neighbour"]), ["person"])

    def test_prohibited_family_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["family"]), ["person"])

    def test_prohibited_homeowner_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["homeowner"]), ["person"])

    def test_prohibited_owner_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["owner"]), ["person"])

    def test_prohibited_known_person_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["known person"]), ["person"])

    def test_prohibited_trusted_visitor_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["trusted visitor"]), ["person"])

    def test_prohibited_familiar_face_maps_to_person(self):
        self.assertEqual(_sanitize_identity_labels(["familiar face"]), ["person"])

    def test_unknown_label_maps_to_person(self):
        # "burglar" is not in the allowlist and not prohibited — maps to person
        self.assertEqual(_sanitize_identity_labels(["burglar"]), ["person"])

    def test_mixed_allowed_and_prohibited(self):
        result = _sanitize_identity_labels(["person", "resident", "vehicle"])
        # resident → person; person and vehicle pass through
        self.assertEqual(result, ["person", "person", "vehicle"])

    def test_case_insensitive_prohibited(self):
        # Input is uppercased — should still be caught after lower()
        result = _sanitize_identity_labels(["RESIDENT"])
        self.assertEqual(result, ["person"])

    def test_empty_list_returns_clear(self):
        self.assertEqual(_sanitize_identity_labels([]), ["clear"])

    def test_blank_labels_skipped_and_falls_back_to_clear(self):
        # Labels that are empty strings after strip → skipped; list becomes empty → ["clear"]
        self.assertEqual(_sanitize_identity_labels([""]), ["clear"])

    def test_whitespace_only_label_falls_back_to_clear(self):
        self.assertEqual(_sanitize_identity_labels(["  "]), ["clear"])


class SanitizeRiskLabelsTests(unittest.TestCase):
    """Tests for _sanitize_risk_labels — enforces allowed risk label set."""

    def test_allowed_threat_label_passes(self):
        result = _sanitize_risk_labels(["intrusion"], threat=True)
        self.assertEqual(result, ["intrusion"])

    def test_allowed_clear_label_passes(self):
        result = _sanitize_risk_labels(["clear"], threat=False)
        self.assertEqual(result, ["clear"])

    def test_allowed_delivery_pattern_passes(self):
        result = _sanitize_risk_labels(["delivery_pattern"], threat=False)
        self.assertEqual(result, ["delivery_pattern"])

    def test_unknown_label_with_threat_maps_to_suspicious_presence(self):
        result = _sanitize_risk_labels(["criminal"], threat=True)
        self.assertEqual(result, ["suspicious_presence"])

    def test_unknown_label_without_threat_maps_to_clear(self):
        result = _sanitize_risk_labels(["criminal"], threat=False)
        self.assertEqual(result, ["clear"])

    def test_empty_list_returns_clear(self):
        result = _sanitize_risk_labels([], threat=False)
        self.assertEqual(result, ["clear"])

    def test_mixed_allowed_and_unknown(self):
        result = _sanitize_risk_labels(["intrusion", "criminal_intent"], threat=True)
        self.assertIn("intrusion", result)
        self.assertIn("suspicious_presence", result)

    def test_hard_threat_label_tamper_passes(self):
        result = _sanitize_risk_labels(["tamper"], threat=True)
        self.assertEqual(result, ["tamper"])

    def test_benign_risk_label_passes(self):
        result = _sanitize_risk_labels(["benign_activity"], threat=False)
        self.assertEqual(result, ["benign_activity"])


class SanitizeDescriptionTests(unittest.TestCase):
    """Tests for _sanitize_description — strips prohibited identity terms from descriptions."""

    def test_clean_description_passes_through(self):
        desc = "A figure is moving toward the front door at night."
        result = _sanitize_description(desc)
        self.assertEqual(result, desc)

    def test_description_with_resident_is_sanitized(self):
        desc = "The resident is entering the front door."
        result = _sanitize_description(desc)
        self.assertEqual(result, "Person or activity detected; identity is unknown from the image.")

    def test_description_with_homeowner_is_sanitized(self):
        desc = "The homeowner has returned from work."
        result = _sanitize_description(desc)
        self.assertEqual(result, "Person or activity detected; identity is unknown from the image.")

    def test_description_with_owner_is_sanitized(self):
        desc = "The owner is checking the mailbox."
        result = _sanitize_description(desc)
        self.assertEqual(result, "Person or activity detected; identity is unknown from the image.")

    def test_description_truncated_at_150_chars(self):
        long_desc = "A" * 200
        result = _sanitize_description(long_desc)
        self.assertEqual(len(result), 150)

    def test_none_input_produces_empty_string(self):
        result = _sanitize_description(None)
        self.assertEqual(result, "")

    def test_non_string_coerced(self):
        result = _sanitize_description(42)
        self.assertEqual(result, "42")

    def test_whitespace_normalization(self):
        result = _sanitize_description("  A   person   was   seen.  ")
        self.assertEqual(result, "A person was seen.")


class SanitizeEvidenceNotesTests(unittest.TestCase):
    """Tests for _sanitize_evidence_notes — enforces max length, strips prohibited terms."""

    def test_clean_note_passes_through(self):
        notes = ["Figure moving toward entry.", "Wearing dark clothing."]
        result = _sanitize_evidence_notes(notes)
        self.assertEqual(result, ["Figure moving toward entry.", "Wearing dark clothing."])

    def test_note_with_prohibited_term_is_dropped(self):
        notes = ["The resident has returned home.", "No package visible."]
        result = _sanitize_evidence_notes(notes)
        self.assertNotIn("The resident has returned home.", result)
        self.assertIn("No package visible.", result)

    def test_at_most_four_notes_returned(self):
        notes = [f"Note {i}" for i in range(10)]
        result = _sanitize_evidence_notes(notes)
        self.assertLessEqual(len(result), 4)

    def test_note_truncated_at_80_chars(self):
        long_note = "A" * 100
        result = _sanitize_evidence_notes([long_note])
        self.assertEqual(len(result[0]), 80)

    def test_non_list_returns_empty_list(self):
        self.assertEqual(_sanitize_evidence_notes("note"), [])
        self.assertEqual(_sanitize_evidence_notes(None), [])

    def test_empty_note_filtered_out(self):
        result = _sanitize_evidence_notes(["", "Valid note."])
        self.assertNotIn("", result)
        self.assertIn("Valid note.", result)


class SanitizeSettingTests(unittest.TestCase):
    """Tests for _sanitize_setting — enforces allowed setting values."""

    def test_allowed_porch_door_passes(self):
        self.assertEqual(_sanitize_setting("porch_door"), "porch_door")

    def test_allowed_driveway_passes(self):
        self.assertEqual(_sanitize_setting("driveway"), "driveway")

    def test_allowed_garage_passes(self):
        self.assertEqual(_sanitize_setting("garage"), "garage")

    def test_allowed_indoor_passes(self):
        self.assertEqual(_sanitize_setting("indoor"), "indoor")

    def test_unknown_setting_maps_to_unknown(self):
        self.assertEqual(_sanitize_setting("bedroom"), "unknown")

    def test_none_maps_to_unknown(self):
        self.assertEqual(_sanitize_setting(None), "unknown")

    def test_case_insensitive(self):
        self.assertEqual(_sanitize_setting("DRIVEWAY"), "driveway")

    def test_unknown_setting_literal_passes(self):
        self.assertEqual(_sanitize_setting("unknown"), "unknown")


class SanitizeObservedActionsTests(unittest.TestCase):
    """Tests for _sanitize_observed_actions — filters to allowed action set."""

    def test_standing_at_entry_allowed(self):
        result = _sanitize_observed_actions(["standing_at_entry"])
        self.assertIn("standing_at_entry", result)

    def test_unknown_action_filtered_out(self):
        result = _sanitize_observed_actions(["doing_cartwheels"])
        self.assertNotIn("doing_cartwheels", result)

    def test_none_returns_default(self):
        result = _sanitize_observed_actions(None)
        self.assertIsInstance(result, list)

    def test_empty_list_returns_default(self):
        result = _sanitize_observed_actions([])
        self.assertIsInstance(result, list)


class SanitizeSpatialTagsTests(unittest.TestCase):
    """Tests for _sanitize_spatial_tags — filters to allowed spatial tag set."""

    def test_at_entry_allowed(self):
        result = _sanitize_spatial_tags(["at_entry"])
        self.assertIn("at_entry", result)

    def test_unknown_tag_filtered(self):
        result = _sanitize_spatial_tags(["rooftop"])
        self.assertNotIn("rooftop", result)

    def test_none_returns_default(self):
        result = _sanitize_spatial_tags(None)
        self.assertIsInstance(result, list)
        self.assertEqual(result, ["unknown_location"])


class SanitizeObjectLabelsTests(unittest.TestCase):
    """Tests for _sanitize_object_labels — filters to allowed object label set."""

    def test_package_allowed(self):
        result = _sanitize_object_labels(["package"])
        self.assertIn("package", result)

    def test_unknown_object_type_filtered(self):
        result = _sanitize_object_labels(["rocket_launcher"])
        self.assertNotIn("rocket_launcher", result)

    def test_none_returns_none_default(self):
        result = _sanitize_object_labels(None)
        self.assertEqual(result, ["none"])


if __name__ == "__main__":
    unittest.main()
