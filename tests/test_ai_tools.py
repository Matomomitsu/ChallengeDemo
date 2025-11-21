from __future__ import annotations

import unittest

from integrations.tuya.ai_tools import _normalize_heuristic_params


class NormalizeHeuristicParamsTests(unittest.TestCase):
    def test_load_dp_code_alias_populates_function_codes(self) -> None:
        params = {"load_dp_code": "switch_led"}
        normalized = _normalize_heuristic_params(params)
        self.assertNotIn("load_dp_code", normalized)
        self.assertIn("function_codes", normalized)
        self.assertEqual(normalized["function_codes"]["switch"], "switch_led")

    def test_existing_function_code_not_overwritten(self) -> None:
        params = {
            "function_codes": {"switch": "switch_1"},
            "load_dp_code": "switch_led",
        }
        normalized = _normalize_heuristic_params(params)
        self.assertEqual(normalized["function_codes"]["switch"], "switch_1")

    def test_threshold_alias_sets_pv_threshold(self) -> None:
        params = {"threshold": 150}
        normalized = _normalize_heuristic_params(params)
        self.assertEqual(normalized["pv_threshold_w"], 150)
        self.assertEqual(normalized["status_value"], 150)

    def test_comparator_alias_recognizes_less_than(self) -> None:
        params = {"comparison": "menor"}
        normalized = _normalize_heuristic_params(params)
        self.assertEqual(normalized["comparator"], "<")

    def test_switch_state_alias_recognizes_desligar(self) -> None:
        params = {"switch_state": "desligar"}
        normalized = _normalize_heuristic_params(params)
        self.assertIn("switch_value", normalized)
        self.assertFalse(normalized["switch_value"])


if __name__ == "__main__":
    unittest.main()
