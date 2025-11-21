"""Backward-compatible shims for legacy imports."""
from __future__ import annotations

from integrations.tuya.client import (
    DEFAULT_TUYA_API_BASE_URL,
    TuyaApiError,
    TuyaClient,
)

__all__ = ["DEFAULT_TUYA_API_BASE_URL", "TuyaApiError", "TuyaClient"]

# Legacy alias
TuyaApiClient = TuyaClient
