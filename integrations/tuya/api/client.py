"""Minimal Tuya Cloud OpenAPI client for device property queries."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

__all__ = ["TuyaApiClient", "TuyaApiError"]

DEFAULT_TUYA_API_BASE_URL = "https://openapi.tuyaus.com"


class TuyaApiError(RuntimeError):
    """Raised when the Tuya Cloud API returns an error response."""


class TuyaApiClient:
    """Helper for authenticating and performing signed Tuya Cloud API calls."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = DEFAULT_TUYA_API_BASE_URL,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required")

        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._base_url = base_url.rstrip("/") or DEFAULT_TUYA_API_BASE_URL

        self._access_token: Optional[str] = None
        self._token_expire_at: float = 0.0

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def get_device_shadow_properties(self, device_id: str) -> Dict[str, Any]:
        """Fetch the latest shadow properties for a device."""
        if not device_id:
            raise ValueError("device_id is required")

        path = f"/v2.0/cloud/thing/{device_id}/shadow/properties"
        data = self._request("GET", path, use_token=True)
        if not data.get("success"):
            raise TuyaApiError(f"Failed to fetch device properties: {data}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise TuyaApiError(f"Unexpected result payload: {data}")
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < (self._token_expire_at - 30):
            return self._access_token

        query = {"grant_type": "1"}
        data = self._request("GET", "/v1.0/token", query=query, use_token=False)

        if not data.get("success"):
            raise TuyaApiError(f"Failed to obtain token: {data}")

        result = data.get("result", {})
        access_token = result.get("access_token")
        expire_time = result.get("expire_time")

        if not access_token or not expire_time:
            raise TuyaApiError(f"Token response missing fields: {data}")

        # expire_time provided in seconds relative to now
        self._access_token = access_token
        self._token_expire_at = now + float(expire_time)
        return access_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        use_token: bool,
    ) -> Dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("path must start with '/' for signing")

        method_upper = method.upper()
        timestamp = str(int(time.time() * 1000))

        access_token = self._get_access_token() if use_token else ""

        query_string = ""
        if query:
            encoded_query = urlencode(sorted(query.items()))
            query_string = f"?{encoded_query}"
        url = f"{self._base_url}{path}{query_string}"

        payload = ""
        if body:
            payload = json.dumps(body, separators=(",", ":"))

        payload_bytes = payload.encode("utf-8") if payload else b""
        content_hash_value = hashlib.sha256(payload_bytes).hexdigest()

        string_to_sign = "\n".join([
            method_upper,
            content_hash_value,
            "",
            f"{path}{query_string}",
        ])

        sign_segments = [self._client_id]
        if access_token:
            sign_segments.append(access_token)
        sign_segments.extend([timestamp, string_to_sign])
        sign_input = "".join(sign_segments)

        signature = (
            hmac.new(
                self._client_secret.encode("utf-8"),
                sign_input.encode("utf-8"),
                hashlib.sha256,
            )
            .hexdigest()
            .upper()
        )

        headers = {
            "client_id": self._client_id,
            "sign": signature,
            "t": timestamp,
            "sign_method": "HMAC-SHA256",
            "Content-Type": "application/json",
        }
        if access_token:
            headers["access_token"] = access_token

        try:
            response = requests.request(
                method_upper,
                url,
                headers=headers,
                json=body if body else None,
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TuyaApiError(f"HTTP error calling Tuya API: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise TuyaApiError(f"Failed to decode Tuya response as JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise TuyaApiError(f"Unexpected response format: {data!r}")
        return data
