from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.goodweApi import GoodweApi

# Importing api.endpoints instantiates GoodweApi(), which logs into the SEMS portal.
# Patch the login so collecting this module never touches the network.
with patch.object(GoodweApi, "GetToken", return_value=None):
    from api import endpoints

_STATION_ENV_VARS = (
    "DEFAULT_POWERSTATION_ID",
    "DEFAULT_POWERSTATION_NAME",
    "DEFAULT_STATION_ID",
    "DEFAULT_STATION_NAME",
)

# Fictitious plants and SOC payload; no real account data.
_PLANTS = {
    "plants": [
        {"powerstation_id": "plant-0001", "stationname": "Planta Demo A"},
        {"powerstation_id": "plant-0002", "stationname": "Planta Demo B"},
    ]
}
_SOC = {"soc": [{"sn": "SN-FAKE-0001", "local": "Demo", "battery_sn": "BAT-FAKE", "power": 87, "status": 1}]}


class BatteryStatusEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(endpoints.router, prefix="/api")
        self.client = TestClient(app)

        # Any real HTTP call made through requests fails the test instead of reaching GoodWe.
        network_guard = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("network access is disabled in tests"),
        )
        network_guard.start()
        self.addCleanup(network_guard.stop)

        self.list_plants = self._patch_api("ListPlants", return_value=_PLANTS)
        self.get_soc = self._patch_api("GetSoc", return_value=_SOC)

    def _patch_api(self, name: str, **kwargs):
        patcher = patch.object(endpoints.goodwe_api, name, **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _set_env(self, **values: str) -> None:
        patcher = patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in _STATION_ENV_VARS:
            if name not in values:
                os.environ.pop(name, None)

    def test_documented_name_selects_plant(self) -> None:
        # Same shape as .env.example: ID left blank, plant chosen by name.
        self._set_env(DEFAULT_POWERSTATION_ID="", DEFAULT_POWERSTATION_NAME="Planta Demo B")

        response = self.client.get("/api/battery/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["powerstation_id"], "plant-0002")
        self.get_soc.assert_called_once_with("plant-0002")

    def test_documented_id_skips_plant_lookup(self) -> None:
        self._set_env(DEFAULT_POWERSTATION_ID="plant-0002")

        response = self.client.get("/api/battery/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.list_plants.assert_not_called()
        self.get_soc.assert_called_once_with("plant-0002")

    def test_without_configured_name_falls_back_to_first_plant(self) -> None:
        self._set_env()

        response = self.client.get("/api/battery/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["powerstation_id"], "plant-0001")

    def test_legacy_env_names_still_supported(self) -> None:
        self._set_env(DEFAULT_STATION_NAME="planta demo b")

        response = self.client.get("/api/battery/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.get_soc.assert_called_once_with("plant-0002")

    def test_documented_name_wins_over_legacy_name(self) -> None:
        self._set_env(DEFAULT_POWERSTATION_NAME="Planta Demo A", DEFAULT_STATION_NAME="Planta Demo B")

        response = self.client.get("/api/battery/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.get_soc.assert_called_once_with("plant-0001")

    def test_unnamed_plant_is_not_matched_by_missing_name(self) -> None:
        self._set_env()
        self.list_plants.return_value = {
            "plants": [
                {"powerstation_id": "plant-0001", "stationname": "Planta Demo A"},
                {"powerstation_id": "plant-0003", "stationname": ""},
            ]
        }

        response = self.client.get("/api/battery/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.get_soc.assert_called_once_with("plant-0001")

    def test_no_plants_returns_404(self) -> None:
        self._set_env()
        self.list_plants.return_value = {"plants": []}

        response = self.client.get("/api/battery/status")

        self.assertEqual(response.status_code, 404)
        self.get_soc.assert_not_called()

    def test_goodwe_failure_returns_502(self) -> None:
        self._set_env(DEFAULT_POWERSTATION_ID="plant-0001")
        self.get_soc.return_value = None

        response = self.client.get("/api/battery/status")

        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
