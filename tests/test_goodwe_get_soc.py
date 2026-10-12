from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import requests

from core.goodweApi import GoodweApi


def _response(status_code: int, payload: dict) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = json.dumps(payload).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    resp.encoding = "utf-8"
    return resp


class GetSocTests(unittest.TestCase):
    def setUp(self) -> None:
        # Fresh singleton with a fake login; the original singleton is restored afterwards.
        # Any real HTTP call made through requests fails the test instead of reaching GoodWe.
        patchers = [
            patch.object(GoodweApi, "_instance", None),
            patch.object(GoodweApi, "GetToken", return_value="fake-token"),
            patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("network access is disabled in tests"),
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        post_patcher = patch("core.goodweApi.requests.post")
        self.post = post_patcher.start()
        self.addCleanup(post_patcher.stop)

        self.api = GoodweApi()

    def test_returns_battery_entries_on_success(self) -> None:
        # Fictitious plant detail payload; extra fields are dropped by extract_soc.
        self.post.return_value = _response(
            200,
            {
                "data": {
                    "soc": [
                        {
                            "sn": "SN-FAKE-0001",
                            "local": "Demo",
                            "battery_sn": "BAT-FAKE",
                            "power": 87,
                            "status": 1,
                            "ignored": "x",
                        }
                    ]
                }
            },
        )

        result = self.api.GetSoc("plant-0001")

        self.assertEqual(
            result,
            {"soc": [{"sn": "SN-FAKE-0001", "local": "Demo", "battery_sn": "BAT-FAKE", "power": 87, "status": 1}]},
        )
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["data"], {"powerStationId": "plant-0001"})

    def test_returns_none_when_plant_detail_request_fails(self) -> None:
        # Empty data triggers one re-login retry; both attempts answer HTTP 500.
        self.post.return_value = _response(500, {"data": None, "msg": "fictitious upstream error"})

        result = self.api.GetSoc("plant-0001")

        self.assertIsNone(result)
        self.assertEqual(self.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
