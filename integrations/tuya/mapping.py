"""Mapping helpers for Tuya device datapoints and functions."""
from __future__ import annotations

from typing import Dict, Optional

from integrations.tuya.models import DeviceLite

# Logical keys → DP codes for known products. Extend as new hardware is added.
DEVICE_PROPERTY_REGISTRY: Dict[str, Dict[str, str]] = {
    # GoodWe inverter (example project productId)
    "xxgnqyeyrzawwwtt": {
        "battery_soc": "Bateria",
        "pv_power": "Producao_Solar_Atual",
        "status": "status",
    }
}

# Logical function names → function codes for actuators (switches, plugs, etc.).
DEVICE_FUNCTION_REGISTRY: Dict[str, Dict[str, str]] = {
    "k43w32veclxmc9lb": {
        "switch": "switch_1",
    }
}

CATEGORY_FALLBACK_PROPERTY_REGISTRY: Dict[str, Dict[str, str]] = {
    "qt": {
        "battery_soc": "Bateria",
        "pv_power": "pv_power",  # placeholder fallback
    }
}

CATEGORY_FALLBACK_FUNCTION_REGISTRY: Dict[str, Dict[str, str]] = {
    "cz": {
        "switch": "switch_1",
    }
}


def resolve_property_code(
    device: DeviceLite,
    logical_key: str,
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Return the DP code for a logical key, consulting overrides, productId, then category."""
    if overrides and logical_key in overrides:
        return overrides[logical_key]

    if device.productId and device.productId in DEVICE_PROPERTY_REGISTRY:
        mapping = DEVICE_PROPERTY_REGISTRY[device.productId]
        if logical_key in mapping:
            return mapping[logical_key]

    if device.category and device.category in CATEGORY_FALLBACK_PROPERTY_REGISTRY:
        mapping = CATEGORY_FALLBACK_PROPERTY_REGISTRY[device.category]
        if logical_key in mapping:
            return mapping[logical_key]

    return None


def resolve_function_code(
    device: DeviceLite,
    logical_key: str,
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Return the executor function code for a logical action."""
    if overrides and logical_key in overrides:
        return overrides[logical_key]

    if device.productId and device.productId in DEVICE_FUNCTION_REGISTRY:
        mapping = DEVICE_FUNCTION_REGISTRY[device.productId]
        if logical_key in mapping:
            return mapping[logical_key]

    if device.category and device.category in CATEGORY_FALLBACK_FUNCTION_REGISTRY:
        mapping = CATEGORY_FALLBACK_FUNCTION_REGISTRY[device.category]
        if logical_key in mapping:
            return mapping[logical_key]

    return None
