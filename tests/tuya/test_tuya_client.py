from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

from integrations.tuya.client import TuyaClient, _RequestContext


def _response(status_code: int, body: str) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = body.encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    resp.encoding = "utf-8"
    resp.url = "https://unit.test"
    return resp


class TuyaClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TuyaClient("client", "secret", base_url="https://unit.test")
        self.client._session = MagicMock()  # type: ignore[assignment]

    def test_request_refreshes_token_after_unauthorized(self) -> None:
        self.client._session.request.side_effect = [
            _response(401, "{}"),
            _response(200, "{\"success\": true, \"result\": {}}"),
        ]

        with patch.object(self.client, "_get_access_token", side_effect=["token-1", "token-2", "token-3"]), patch.object(
            self.client, "_clear_access_token"
        ) as clear_token:
            result = self.client._request("GET", "/v1.0/test", use_token=True)

        self.assertEqual(result, {"success": True, "result": {}})
        self.assertEqual(self.client._session.request.call_count, 2)
        clear_token.assert_called_once()

    def test_rate_limit_backoff_retries(self) -> None:
        self.client._session.request.side_effect = [
            _response(429, "{}"),
            _response(200, "{\"success\": true}"),
        ]
        with patch.object(self.client, "_get_access_token", return_value="token"), patch("integrations.tuya.client.time.sleep") as sleep:
            result = self.client._request("GET", "/v1.0/test", use_token=True)
        self.assertEqual(result, {"success": True})
        sleep.assert_called_once()

    def test_build_request_signature_deterministic(self) -> None:
        context = _RequestContext(
            method="GET",
            path="/v1.0/test",
            query={"a": "1"},
            body=None,
            use_token=True,
        )
        with patch("integrations.tuya.client.time.time", return_value=1700000000.0):
            headers, url, payload = self.client._build_request(access_token="token", context=context)
        self.assertIn("sign", headers)
        self.assertEqual(url, "https://unit.test/v1.0/test?a=1")
        self.assertEqual(payload, None)
        self.assertEqual(headers["client_id"], "client")
        self.assertEqual(headers["access_token"], "token")


if __name__ == "__main__":
    unittest.main()
