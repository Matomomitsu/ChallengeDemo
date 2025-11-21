from __future__ import annotations

import unittest

from integrations.tuya.heuristics import (
    HeuristicContext,
    build_heuristic_proposals,
    heuristic_battery_protect,
    heuristic_battery_surplus,
    heuristic_night_guard,
    heuristic_solar_surplus,
)
from integrations.tuya.models import DeviceLite, Property


class HeuristicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inverter = DeviceLite.model_validate(
            {
                "id": "inverter",
                "productId": "xxgnqyeyrzawwwtt",
                "category": "qt",
                "name": "Inverter",
            }
        )
        self.plug = DeviceLite.model_validate(
            {
                "id": "plug",
                "productId": "k43w32veclxmc9lb",
                "category": "cz",
                "name": "Plug",
            }
        )
        self.properties = {
            "inverter": {
                "Bateria": Property.model_validate({"code": "Bateria", "value": 75}),
                "Producao_Solar_Atual": Property.model_validate({"code": "Producao_Solar_Atual", "value": 900}),
            }
        }
        self.config = {
            "heuristics": {
                "battery_protect": {
                    "inverter_device_id": "inverter",
                    "load_device_id": "plug",
                    "threshold": 90,
                    "property_codes": {"battery_soc": "Bateria"},
                    "function_codes": {"switch": "switch_1"},
                },
                "solar_surplus": {
                    "inverter_device_id": "inverter",
                    "load_device_id": "plug",
                    "pv_threshold_w": 800,
                    "property_codes": {"pv_power": "Producao_Solar_Atual"},
                    "function_codes": {"switch": "switch_1"},
                },
                "night_guard": {
                    "inverter_device_id": "inverter",
                    "load_device_id": "plug",
                    "property_codes": {"pv_power": "Producao_Solar_Atual"},
                    "function_codes": {"switch": "switch_1"},
                    "start": "18:00",
                    "end": "06:00",
                },
                "battery_surplus": {
                    "inverter_device_id": "inverter",
                    "load_device_id": "plug",
                    "threshold": 80,
                    "property_codes": {"battery_soc": "Bateria"},
                    "function_codes": {"switch": "switch_1"},
                },
            }
        }
        self.context = HeuristicContext(
            space_id="space",
            devices={"inverter": self.inverter, "plug": self.plug},
            properties=self.properties,
            config=self.config,
        )

    def test_battery_protect_builds_condition_and_action(self) -> None:
        proposal = heuristic_battery_protect(self.context, self.config["heuristics"]["battery_protect"])
        self.assertEqual(proposal.conditions[0].expr.status_code, "Bateria")
        self.assertEqual(proposal.conditions[0].expr.comparator, "<")
        self.assertEqual(proposal.actions[0].executor_property.function_code, "switch_1")
        scene = proposal.to_scene_rule("space")
        self.assertEqual(scene.decision_expr, "and")
        self.assertEqual(scene.conditions[0].expr.status_value, 90)
        self.assertEqual(scene.conditions[0].code, 1)

    def test_battery_surplus_defaults_to_switch_on(self) -> None:
        proposal = heuristic_battery_surplus(self.context, self.config["heuristics"]["battery_surplus"])
        self.assertEqual(proposal.conditions[0].expr.status_code, "Bateria")
        self.assertEqual(proposal.conditions[0].expr.comparator, ">")
        self.assertTrue(proposal.actions[0].executor_property.function_value)

    def test_solar_surplus_threshold(self) -> None:
        proposal = heuristic_solar_surplus(self.context, self.config["heuristics"]["solar_surplus"])
        self.assertEqual(proposal.conditions[0].expr.status_value, 800)
        self.assertTrue(proposal.actions[0].executor_property.function_value)

    def test_solar_surplus_threshold_alias_and_switch_override(self) -> None:
        params = {
            "inverter_device_id": "inverter",
            "load_device_id": "plug",
            "threshold": 500,
            "comparator": "<",
            "switch_value": False,
            "property_codes": {"pv_power": "Producao_Solar_Atual"},
            "function_codes": {"switch": "switch_1"},
        }
        proposal = heuristic_solar_surplus(self.context, params)
        self.assertEqual(proposal.conditions[0].expr.status_value, 500)
        self.assertEqual(proposal.conditions[0].expr.comparator, "<")
        self.assertFalse(proposal.actions[0].executor_property.function_value)

    def test_night_guard_effective_time(self) -> None:
        proposal = heuristic_night_guard(self.context, self.config["heuristics"]["night_guard"])
        self.assertIsNotNone(proposal.effective_time)
        self.assertEqual(proposal.effective_time.start, "18:00")

    def test_build_heuristic_proposals(self) -> None:
        proposals = build_heuristic_proposals(self.context, ["battery_protect", "solar_surplus"])
        self.assertEqual(len(proposals), 2)

    def test_switch_function_fallback_uses_switch_led(self) -> None:
        properties = dict(self.properties)
        properties["plug"] = {
            "switch_led": Property.model_validate({"code": "switch_led", "value": False})
        }
        params = {
            "inverter_device_id": "inverter",
            "load_device_id": "plug",
            "pv_threshold_w": 800,
            "property_codes": {"pv_power": "Producao_Solar_Atual"},
        }
        fallback_context = HeuristicContext(
            space_id="space",
            devices=self.context.devices,
            properties=properties,
            config={"heuristics": {}},
        )
        proposal = heuristic_solar_surplus(fallback_context, params)
        self.assertEqual(proposal.actions[0].executor_property.function_code, "switch_led")

    def test_solar_deficit_builds_inverse_logic(self) -> None:
        params = {
            "inverter_device_id": "inverter",
            "load_device_id": "plug",
            "threshold": 600,
            "comparator": "<",
            "switch_value": False,
            "property_codes": {"pv_power": "Producao_Solar_Atual"},
            "function_codes": {"switch": "switch_1"},
        }
        from integrations.tuya.heuristics import heuristic_solar_deficit

        proposal = heuristic_solar_deficit(self.context, params)
        self.assertEqual(proposal.conditions[0].expr.status_value, 600)
        self.assertEqual(proposal.conditions[0].expr.comparator, "<")
        self.assertFalse(proposal.actions[0].executor_property.function_value)

    def test_extra_actions_are_appended(self) -> None:
        params = {
            "inverter_device_id": "inverter",
            "load_device_id": "plug",
            "pv_threshold_w": 800,
            "property_codes": {"pv_power": "Producao_Solar_Atual"},
            "function_codes": {"switch": "switch_1"},
            "extra_actions": [
                {"entity_id": "plug", "function_code": "switch_1", "function_value": True},
                {"entity_id": "inverter", "function_code": "status", "function_value": "note"},
            ],
        }
        proposal = heuristic_solar_surplus(self.context, params)
        self.assertGreaterEqual(len(proposal.actions), 3)


if __name__ == "__main__":
    unittest.main()
