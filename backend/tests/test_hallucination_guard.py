from __future__ import annotations

import unittest

from backend.agent.hallucination_guard import detect_hallucination_markers, strip_capability_claims


class StripCapabilityClaimsTests(unittest.TestCase):
    """Tests for strip_capability_claims — removes AI self-capability language."""

    def test_strips_i_can_phrase(self):
        result = strip_capability_claims("I can detect intruders in real time.")
        self.assertNotIn("I can", result)
        self.assertIn("[redacted]", result)

    def test_strips_i_will_notify(self):
        result = strip_capability_claims("I will notify the homeowner immediately.")
        self.assertIn("[redacted]", result)

    def test_strips_i_have_access(self):
        result = strip_capability_claims("I have access to the camera feed.")
        self.assertIn("[redacted]", result)

    def test_strips_i_am_able_to(self):
        result = strip_capability_claims("I am able to track this person.")
        self.assertIn("[redacted]", result)

    def test_strips_the_system_will(self):
        result = strip_capability_claims("The system will escalate this alert.")
        self.assertIn("[redacted]", result)

    def test_strips_i_will_alert(self):
        result = strip_capability_claims("I will alert authorities now.")
        self.assertIn("[redacted]", result)

    def test_strips_i_will_send(self):
        result = strip_capability_claims("I will send a notification.")
        self.assertIn("[redacted]", result)

    def test_strips_i_have_the_ability(self):
        result = strip_capability_claims("I have the ability to identify faces.")
        self.assertIn("[redacted]", result)

    def test_strips_i_can_see(self):
        result = strip_capability_claims("I can see movement near the door.")
        self.assertIn("[redacted]", result)

    def test_strips_i_detect(self):
        result = strip_capability_claims("I detect suspicious activity at the perimeter.")
        self.assertIn("[redacted]", result)

    def test_strips_i_recognise(self):
        result = strip_capability_claims("I recognise a familiar pattern here.")
        self.assertIn("[redacted]", result)

    def test_strips_i_recognize(self):
        result = strip_capability_claims("I recognize this individual from prior footage.")
        self.assertIn("[redacted]", result)

    def test_case_insensitive(self):
        result = strip_capability_claims("I CAN DETECT movement.")
        self.assertIn("[redacted]", result)

    def test_preserves_non_capability_text(self):
        rationale = (
            "SIGNAL: movement near front door. "
            "EVIDENCE: figure visible in frame. "
            "UNCERTAINTY: low light conditions. "
            "DECISION: alert."
        )
        result = strip_capability_claims(rationale)
        self.assertEqual(result, rationale)

    def test_multiple_capabilities_in_one_string(self):
        text = "I can detect this. I will notify the homeowner. I have access to history."
        result = strip_capability_claims(text)
        self.assertEqual(result.count("[redacted]"), 3)

    def test_returns_none_passthrough(self):
        # None/empty should pass through without error
        self.assertIsNone(strip_capability_claims(None))

    def test_returns_empty_string_passthrough(self):
        self.assertEqual(strip_capability_claims(""), "")

    def test_returns_non_string_passthrough(self):
        # Non-string should be returned as-is
        self.assertEqual(strip_capability_claims(42), 42)


class DetectHallucinationMarkersTests(unittest.TestCase):
    """Tests for detect_hallucination_markers — observability only, no behavior change."""

    def test_detects_known_person(self):
        matches = detect_hallucination_markers("This is a known person at the front door.")
        self.assertTrue(any("known person" in m.lower() for m in matches))

    def test_detects_familiar_face(self):
        matches = detect_hallucination_markers("The familiar face was seen on camera.")
        self.assertTrue(any("familiar face" in m.lower() for m in matches))

    def test_detects_trusted_visitor(self):
        matches = detect_hallucination_markers("This appears to be a trusted visitor.")
        self.assertTrue(any("trusted visitor" in m.lower() for m in matches))

    def test_detects_resident(self):
        matches = detect_hallucination_markers("Movement consistent with resident activity.")
        self.assertTrue(len(matches) > 0)

    def test_detects_homeowner(self):
        matches = detect_hallucination_markers("This looks like the homeowner returning.")
        self.assertTrue(len(matches) > 0)

    def test_detects_owner(self):
        matches = detect_hallucination_markers("The owner was seen near the garage.")
        self.assertTrue(len(matches) > 0)

    def test_detects_capability_claims(self):
        matches = detect_hallucination_markers("I can see a person at the door.")
        self.assertTrue(len(matches) > 0)

    def test_clean_rationale_returns_empty_list(self):
        rationale = (
            "SIGNAL: unidentified person at perimeter. "
            "EVIDENCE: figure moving toward entry. "
            "UNCERTAINTY: low light. "
            "DECISION: alert."
        )
        matches = detect_hallucination_markers(rationale)
        self.assertEqual(matches, [])

    def test_deduplicates_matches(self):
        text = "The resident was seen. The resident left. resident activity."
        matches = detect_hallucination_markers(text)
        lowered = [m.lower() for m in matches]
        self.assertEqual(len(lowered), len(set(lowered)))

    def test_none_returns_empty_list(self):
        self.assertEqual(detect_hallucination_markers(None), [])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(detect_hallucination_markers(""), [])

    def test_non_string_returns_empty_list(self):
        self.assertEqual(detect_hallucination_markers(123), [])


if __name__ == "__main__":
    unittest.main()
