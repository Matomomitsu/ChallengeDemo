"""Mapping helpers for Tuya device datapoints and functions.

Enhancements:
- Supports product/category registries (built-in)
- Supports device-specific overrides via optional YAML config files
  at `configs/tuya_device_mappings.yaml` or `configs/device_mappings.yaml`.
"""
from __future__ import annotations

from typing import Dict, Optional

from integrations.tuya.models import DeviceLite
import pathlib
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional at import time
    yaml = None  # type: ignore

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
        "switch": "switch_led",
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
        "switch": "switch_led",
    }
}


DEVICE_ID_PROPERTY_REGISTRY: Dict[str, Dict[str, str]] = {}
DEVICE_ID_FUNCTION_REGISTRY: Dict[str, Dict[str, str]] = {}


def _load_device_mapping_file() -> None:
    global DEVICE_ID_PROPERTY_REGISTRY, DEVICE_ID_FUNCTION_REGISTRY
    candidates = [
        pathlib.Path("configs/tuya_device_mappings.yaml"),
        pathlib.Path("configs/device_mappings.yaml"),
    ]
    for path in candidates:
        try:
            if path.exists() and yaml:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict):
                    continue
                dev_props = data.get("device_properties") or {}
                dev_funcs = data.get("device_functions") or {}
                if isinstance(dev_props, dict):
                    DEVICE_ID_PROPERTY_REGISTRY.update({str(k): dict(v) for k, v in dev_props.items() if isinstance(v, dict)})
                if isinstance(dev_funcs, dict):
                    DEVICE_ID_FUNCTION_REGISTRY.update({str(k): dict(v) for k, v in dev_funcs.items() if isinstance(v, dict)})
                break
        except Exception:
            # Ignore mapping file errors; fallback registries still apply
            pass


# Load mappings once at import time (best effort)
_load_device_mapping_file()


def resolve_property_code(
    device: DeviceLite,
    logical_key: str,
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Return the DP code for a logical key, consulting overrides, productId, then category."""
    if overrides and logical_key in overrides:
        return overrides[logical_key]

    # Device-specific registry (highest priority after explicit overrides)
    if device.id and device.id in DEVICE_ID_PROPERTY_REGISTRY:
        mapping = DEVICE_ID_PROPERTY_REGISTRY[device.id]
        if logical_key in mapping:
            return mapping[logical_key]

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

    # Device-specific registry (highest priority after explicit overrides)
    if device.id and device.id in DEVICE_ID_FUNCTION_REGISTRY:
        mapping = DEVICE_ID_FUNCTION_REGISTRY[device.id]
        if logical_key in mapping:
            return mapping[logical_key]

    if device.productId and device.productId in DEVICE_FUNCTION_REGISTRY:
        mapping = DEVICE_FUNCTION_REGISTRY[device.productId]
        if logical_key in mapping:
            return mapping[logical_key]

    if device.category and device.category in CATEGORY_FALLBACK_FUNCTION_REGISTRY:
        mapping = CATEGORY_FALLBACK_FUNCTION_REGISTRY[device.category]
        if logical_key in mapping:
            return mapping[logical_key]

    return None
