from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.goodweApi import GoodweApi

# Importing api.endpoints instantiates GoodweApi(), which logs into the SEMS portal.
# Patch the login so collecting this module never touches the network.
with patch.object(GoodweApi, "GetToken", return_value=None):
    from api import endpoints

# Same shape call_geminiapi returns; fictitious content.
_GEMINI_RESULT = {
    "response": "Hoje a planta de demonstração gerou 12 kWh.",
    "functions_preview": [],
    "fallback_to_default": False,
    "used_powerstation_id": None,
}


class GoogleWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(endpoints.router, prefix="/api")
        self.client = TestClient(app)

        # Any real HTTP call made through requests fails the test instead of reaching a service.
        network_guard = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("network access is disabled in tests"),
        )
        network_guard.start()
        self.addCleanup(network_guard.stop)

        gemini_patcher = patch.object(endpoints, "call_geminiapi", new=AsyncMock(return_value=_GEMINI_RESULT))
        self.call_gemini = gemini_patcher.start()
        self.addCleanup(gemini_patcher.stop)

    def test_fulfillment_carries_response_text(self) -> None:
        response = self.client.post(
            "/api/google/webhook",
            json={"queryResult": {"queryText": "Quanto a planta gerou hoje?"}},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.call_gemini.assert_awaited_once_with("Quanto a planta gerou hoje?")
        body = response.json()
        expected = _GEMINI_RESULT["response"]
        self.assertEqual(body["fulfillmentMessages"], [{"text": {"text": [expected]}}])
        speech = body["payload"]["google"]["richResponse"]["items"][0]["simpleResponse"]["textToSpeech"]
        self.assertEqual(speech, expected)
        self.assertTrue(body["payload"]["google"]["expectUserResponse"])


if __name__ == "__main__":
    unittest.main()
