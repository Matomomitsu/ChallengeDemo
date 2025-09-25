"""HTTP client for Tuya Cloud Open API (automation workflows)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlencode

import requests

LOGGER = logging.getLogger(__name__)

DEFAULT_TUYA_API_BASE_URL = os.getenv("TUYA_API_BASE_URL", "https://openapi.tuyaus.com")
TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 15
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_BASE_SECONDS = 1.5

__all__ = ["TuyaClient", "TuyaApiError", "DEFAULT_TUYA_API_BASE_URL"]


class TuyaApiError(RuntimeError):
    """Raised when the Tuya Cloud API reports a failure or unexpected payload."""


@dataclass
class _RequestContext:
    method: str
    path: str
    query: Optional[Dict[str, Any]]
    body: Optional[Dict[str, Any]]
    use_token: bool


class TuyaClient:
    """Tuya Cloud client supporting device discovery, properties, and scene automation."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        base_url: str | None = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required")

        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._base_url = (base_url or DEFAULT_TUYA_API_BASE_URL).rstrip("/")

        self._session = session or requests.Session()
        self._access_token: Optional[str] = None
        self._token_expire_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def list_space_devices(
        self,
        space_ids: Iterable[str],
        *,
        is_recursion: bool = False,
        page_size: int = 20,
        last_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return devices attached to the supplied Tuya space ids."""

        query = {
            "space_ids": ",".join(sorted({s.strip() for s in space_ids if s.strip()})),
            "is_recursion": str(is_recursion).lower(),
            "page_size": str(page_size),
        }
        if last_id:
            query["last_id"] = last_id

        devices: List[Dict[str, Any]] = []
        has_more = True
        cursor: Optional[str] = last_id

        while has_more:
            if cursor:
                query["last_id"] = cursor

            payload = self._request("GET", "/v2.0/cloud/thing/space/device", query=query, use_token=True)
            result = payload.get("result")

            batch: List[Dict[str, Any]]
            if isinstance(result, dict):
                batch = result.get("list", []) or []
                has_more = bool(result.get("has_more"))
                cursor = result.get("last_id") or (batch[-1]["id"] if batch else None)
            elif isinstance(result, list):
                batch = result
                has_more = False if len(batch) < page_size else bool(cursor)
                cursor = None
            else:
                raise TuyaApiError(f"Unexpected list_space_devices payload: {payload}")

            devices.extend(batch)

            if not has_more:
                break

        return devices

    def get_device_shadow(self, device_id: str, codes: Optional[List[str]] = None) -> Dict[str, Any]:
        """Retrieve device shadow properties."""
        if not device_id:
            raise ValueError("device_id is required")

        query: Dict[str, Any] | None = None
        if codes:
            query = {"codes": ",".join(codes)}

        payload = self._request(
            "GET",
            f"/v2.0/cloud/thing/{device_id}/shadow/properties",
            query=query,
            use_token=True,
        )
        if not payload.get("success"):
            raise TuyaApiError(f"Failed to fetch device shadow: {payload}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise TuyaApiError(f"Unexpected shadow result: {payload}")
        return result

    # Legacy compatibility helper
    def get_device_shadow_properties(self, device_id: str, codes: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.get_device_shadow(device_id, codes)

    def list_scenes(self, space_id: str) -> List[Dict[str, Any]]:
        if not space_id:
            raise ValueError("space_id is required")
        payload = self._request(
            "GET",
            "/v2.0/cloud/scene/rule",
            query={"space_id": space_id},
            use_token=True,
        )
        if not payload.get("success"):
            raise TuyaApiError(f"Failed to list scenes: {payload}")
        result = payload.get("result", {})
        scenes = result.get("list", []) if isinstance(result, dict) else result
        return scenes or []

    def get_scene(self, rule_id: str) -> Dict[str, Any]:
        if not rule_id:
            raise ValueError("rule_id is required")
        payload = self._request("GET", f"/v2.0/cloud/scene/rule/{rule_id}", use_token=True)
        if not payload.get("success"):
            raise TuyaApiError(f"Failed to fetch scene detail: {payload}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise TuyaApiError(f"Unexpected scene detail: {payload}")
        return result

    def create_scene(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        response = self._request("POST", "/v2.0/cloud/scene/rule", body=payload, use_token=True)
        if not response.get("success"):
            raise TuyaApiError(f"Failed to create scene: {response}")
        return response.get("result", {})

    def update_scene(self, rule_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not rule_id:
            raise ValueError("rule_id is required")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        response = self._request(
            "PUT",
            f"/v2.0/cloud/scene/rule/{rule_id}",
            body=payload,
            use_token=True,
        )
        if not response.get("success"):
            raise TuyaApiError(f"Failed to update scene: {response}")
        return response.get("result", {})

    def delete_scenes(self, ids: Sequence[str], space_id: str) -> Dict[str, Any]:
        ids_value = ",".join(sorted({rule_id.strip() for rule_id in ids if rule_id.strip()}))
        if not ids_value:
            raise ValueError("At least one rule id is required to delete scenes")
        if not space_id:
            raise ValueError("space_id is required to delete scenes")
        response = self._request(
            "DELETE",
            "/v2.0/cloud/scene/rule",
            query={"ids": ids_value, "space_id": space_id},
            use_token=True,
        )
        if not response.get("success"):
            raise TuyaApiError(f"Failed to delete scenes: {response}")
        return response.get("result", {})

    def set_scenes_state(self, ids: Sequence[str], is_enable: bool) -> Dict[str, Any]:
        ids_value = ",".join(sorted({rule_id.strip() for rule_id in ids if rule_id.strip()}))
        if not ids_value:
            raise ValueError("At least one rule id is required to set scene state")
        body = {"ids": ids_value, "is_enable": bool(is_enable)}
        response = self._request(
            "PUT",
            "/v2.0/cloud/scene/rule/state",
            body=body,
            use_token=True,
        )
        if not response.get("success"):
            raise TuyaApiError(f"Failed to set scene state: {response}")
        return response.get("result", {})

    def set_scene_state(self, rule_id: str, is_enable: bool) -> Dict[str, Any]:
        if not rule_id:
            raise ValueError("rule_id is required")
        return self.set_scenes_state([rule_id], is_enable)

    def trigger_scene(self, rule_id: str) -> Dict[str, Any]:
        if not rule_id:
            raise ValueError("rule_id is required")
        response = self._request(
            "POST",
            f"/v2.0/cloud/scene/rule/{rule_id}/actions/trigger",
            use_token=True,
        )
        if not response.get("success"):
            raise TuyaApiError(f"Failed to trigger scene: {response}")
        return response.get("result", {})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _clear_access_token(self) -> None:
        self._access_token = None
        self._token_expire_at = 0.0

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = time.time()
        if not force_refresh and self._access_token and now < (self._token_expire_at - TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS):
            return self._access_token

        payload = self._request(
            "GET",
            "/v1.0/token",
            query={"grant_type": "1"},
            use_token=False,
        )

        if not payload.get("success"):
            raise TuyaApiError(f"Token request failed: {payload}")

        result = payload.get("result", {})
        access_token = result.get("access_token")
        expire_time = result.get("expire_time")
        if not access_token or expire_time is None:
            raise TuyaApiError(f"Token response missing fields: {payload}")

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

        context = _RequestContext(method=method.upper(), path=path, query=query, body=body, use_token=use_token)

        attempt = 0
        refreshed = False
        while True:
            attempt += 1
            access_token = self._get_access_token() if context.use_token else ""
            headers, url, payload = self._build_request(access_token=access_token, context=context)

            try:
                response = self._session.request(
                    context.method,
                    url,
                    headers=headers,
                    data=payload if payload else None,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise TuyaApiError(f"HTTP request to Tuya API failed: {exc}") from exc

            if response.status_code in {401, 403} and context.use_token:
                if refreshed:
                    raise TuyaApiError(f"Unauthorized after token refresh: {response.text}")
                LOGGER.warning("Tuya API returned %s; refreshing token and retrying", response.status_code)
                self._clear_access_token()
                self._get_access_token(force_refresh=True)
                refreshed = True
                continue

            if response.status_code == 429 and attempt <= MAX_RATE_LIMIT_RETRIES:
                backoff = RATE_LIMIT_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                LOGGER.warning("Tuya API rate limited request; backing off %.2fs", backoff)
                time.sleep(backoff)
                continue

            if response.status_code >= 400:
                raise TuyaApiError(
                    f"Tuya API error {response.status_code}: {response.text}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise TuyaApiError(f"Failed to decode Tuya response as JSON: {exc}") from exc

            if not isinstance(data, dict):
                raise TuyaApiError(f"Unexpected response envelope: {data!r}")

            return data

    def _build_request(
        self,
        *,
        access_token: str,
        context: _RequestContext,
    ) -> tuple[Dict[str, str], str, Optional[str]]:
        timestamp = str(int(time.time() * 1000))

        query_items = context.query or {}
        encoded_query = urlencode(sorted((k, v) for k, v in query_items.items() if v is not None))
        query_string = f"?{encoded_query}" if encoded_query else ""
        url = f"{self._base_url}{context.path}{query_string}"

        payload_str = ""
        if context.body is not None:
            payload_str = json.dumps(context.body, separators=(",", ":"))
        payload_bytes = payload_str.encode("utf-8") if payload_str else b""
        content_hash = hashlib.sha256(payload_bytes).hexdigest()

        string_to_sign = "\n".join(
            [
                context.method,
                content_hash,
                "",
                f"{context.path}{query_string}",
            ]
        )

        sign_input_parts = [self._client_id]
        if access_token:
            sign_input_parts.append(access_token)
        sign_input_parts.extend([timestamp, string_to_sign])
        sign_input = "".join(sign_input_parts)

        signature = hmac.new(
            self._client_secret.encode("utf-8"),
            sign_input.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()

        headers = {
            "client_id": self._client_id,
            "sign": signature,
            "t": timestamp,
            "sign_method": "HMAC-SHA256",
            "Content-Type": "application/json",
        }
        if access_token:
            headers["access_token"] = access_token

        return headers, url, payload_str if payload_str else None
