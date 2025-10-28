import asyncio
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from integrations.tuya.ai_tools import (
    create_and_enable_automation,
    describe_space,
    inspect_device,
)


_SPACE_CACHE_TTL_SECONDS = int(os.getenv("ALEXA_FASTPATH_SPACE_CACHE_TTL", "60"))
_PROPERTIES_CACHE_TTL_SECONDS = int(os.getenv("ALEXA_FASTPATH_PROPERTIES_CACHE_TTL", "120"))
_FASTPATH_ENABLED = os.getenv("ALEXA_FASTPATH_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


@dataclass
class DeviceSnapshot:
    device_id: str
    friendly_name: str
    normalized_name: str
    category: Optional[str]


_SPACE_CACHE: Dict[str, Any] = {
    "space_id": None,
    "timestamp": 0.0,
    "devices": [],
}
_PROPERTIES_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _normalize_text(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9% ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _token_set(value: str) -> set[str]:
    return {token for token in value.split() if token}


def _load_devices(space_id: str) -> List[DeviceSnapshot]:
    now = time.time()
    cache_space = _SPACE_CACHE.get("space_id")
    if cache_space == space_id and (now - _SPACE_CACHE.get("timestamp", 0.0)) < _SPACE_CACHE_TTL_SECONDS:
        return list(_SPACE_CACHE.get("devices") or [])

    payload = describe_space(space_id)
    devices_payload = payload.get("devices") or []
    devices: List[DeviceSnapshot] = []
    for device in devices_payload:
        device_id = device.get("id")
        if not device_id:
            continue
        friendly = (device.get("customName") or device.get("name") or "").strip() or "Dispositivo"
        devices.append(
            DeviceSnapshot(
                device_id=device_id,
                friendly_name=friendly,
                normalized_name=_normalize_text(friendly),
                category=device.get("category"),
            )
        )

    _SPACE_CACHE["space_id"] = space_id
    _SPACE_CACHE["timestamp"] = now
    _SPACE_CACHE["devices"] = devices
    return devices


def _load_properties(device_id: str) -> Dict[str, Any]:
    now = time.time()
    cached = _PROPERTIES_CACHE.get(device_id)
    if cached and (now - cached[0]) < _PROPERTIES_CACHE_TTL_SECONDS:
        return dict(cached[1])

    payload = inspect_device(device_id)
    properties = payload.get("properties") or {}
    _PROPERTIES_CACHE[device_id] = (now, properties)
    return dict(properties)


def _has_fastpath_keywords(normalized_input: str) -> bool:
    if not normalized_input:
        return False
    required_terms = ("cena", "automacao", "automatizacao", "automatizar")
    if not any(term in normalized_input for term in required_terms):
        return False
    if "bateria" not in normalized_input:
        return False
    if not any(keyword in normalized_input for keyword in ("liga", "ligar", "ativa", "ativar", "desliga", "desligar")):
        return False
    return True


def _extract_action(normalized_input: str) -> Optional[bool]:
    turn_on_keywords = ("liga", "ligar", "ativa", "ativar", "aciona", "acionar", "start")
    turn_off_keywords = ("desliga", "desligar", "desativa", "desativar", "apaga", "apagar", "stop")

    if any(keyword in normalized_input for keyword in turn_on_keywords):
        return True
    if any(keyword in normalized_input for keyword in turn_off_keywords):
        return False
    return None


def _extract_threshold(normalized_input: str) -> Optional[int]:
    match = re.search(r"(\d{1,3})\s*(?:%|por cento|percento|percent)", normalized_input)
    if not match:
        match = re.search(r"(\d{1,3})", normalized_input)
    if not match:
        return None
    value = int(match.group(1))
    value = max(0, min(value, 100))
    return value


def _extract_comparator(normalized_input: str) -> str:
    if any(keyword in normalized_input for keyword in ("menor", "abaixo", "inferior")):
        return "<"
    if any(keyword in normalized_input for keyword in ("igual", "igual a", "igualdade", "exatamente")):
        return "=="
    return ">"


def _match_device(devices: List[DeviceSnapshot], normalized_input: str) -> Optional[DeviceSnapshot]:
    if not devices:
        return None
    input_tokens = _token_set(normalized_input)
    for device in devices:
        tokens = _token_set(device.normalized_name)
        if tokens and tokens.issubset(input_tokens):
            return device
    for device in devices:
        if device.normalized_name and device.normalized_name in normalized_input:
            return device
    return None


def _pick_condition_device(devices: List[DeviceSnapshot]) -> Optional[DeviceSnapshot]:
    priority_keywords = ("inversor", "inversor solar", "bateria", "solar", "energia")
    for keyword in priority_keywords:
        for device in devices:
            if keyword in device.normalized_name:
                return device
    for device in devices:
        if device.category in {"qt", "energy", "power"}:
            return device
    return devices[0] if devices else None


def _detect_switch_code(properties: Dict[str, Any]) -> Optional[str]:
    if not properties:
        return None
    fallback = None
    for code, descriptor in properties.items():
        code_value = descriptor.get("code") or code
        value = descriptor.get("value")
        prop_type = descriptor.get("type") or ""
        code_lower = str(code_value).lower()
        if isinstance(value, bool):
            return code_value
        if code_lower.startswith("switch"):
            fallback = code_value
        if isinstance(value, (int, float)) and prop_type.lower() == "bool":
            return code_value
    return fallback


def _detect_battery_code(properties: Dict[str, Any]) -> Optional[str]:
    if not properties:
        return None
    keywords = ("bateria", "battery", "soc")
    fallback = None
    for code, descriptor in properties.items():
        code_value = descriptor.get("code") or code
        label = descriptor.get("custom_name") or ""
        combined = f"{_normalize_text(code_value)} {_normalize_text(label)}"
        if any(keyword in combined for keyword in keywords):
            return code_value
        if fallback is None and isinstance(descriptor.get("value"), (int, float)):
            fallback = code_value
    return fallback


def _compose_rule_name(*, action_on: bool, device_name: str, comparator: str, threshold: int) -> str:
    action = "Ligar" if action_on else "Desligar"
    comparator_label = {
        ">": "acima de",
        "<": "abaixo de",
        "==": "igual a",
    }.get(comparator, "acima de")
    return f"{action} {device_name} com Bateria {comparator_label} {threshold}%"


def _build_success_message(
    *,
    action_on: bool,
    device_name: str,
    comparator: str,
    threshold: int,
    rule_name: str,
) -> str:
    action_phrase = "ligar" if action_on else "desligar"
    comparator_phrase = {
        ">": "acima de",
        "<": "abaixo de",
        "==": "igual a",
    }.get(comparator, "acima de")
    return (
        f"Tudo certo! A automação '{rule_name}' foi criada e ativada. "
        f"Vou {action_phrase} {device_name} quando a bateria ficar {comparator_phrase} {threshold}%."
    )


def _process_fastpath_sync(user_input: str, normalized_input: str) -> Optional[str]:
    space_id = os.getenv("TUYA_SPACE_ID")
    if not space_id:
        return None

    devices = _load_devices(space_id)
    if not devices:
        return None

    action_state = _extract_action(normalized_input)
    if action_state is None:
        return None

    target_device = _match_device(devices, normalized_input)
    if not target_device:
        return None
    if "bateria" in target_device.normalized_name or "inversor" in target_device.normalized_name:
        return None

    comparator = _extract_comparator(normalized_input)
    threshold = _extract_threshold(normalized_input)
    if threshold is None:
        return None

    condition_device = _pick_condition_device(devices)
    if not condition_device:
        return None

    action_properties = _load_properties(target_device.device_id)
    switch_code = _detect_switch_code(action_properties)
    if not switch_code:
        return None

    condition_properties = _load_properties(condition_device.device_id)
    status_code = _detect_battery_code(condition_properties)
    if not status_code:
        return None

    rule_name = _compose_rule_name(
        action_on=action_state,
        device_name=target_device.friendly_name,
        comparator=comparator,
        threshold=threshold,
    )

    payload = {
        "space_id": space_id,
        "name": rule_name,
        "type": "automation",
        "decision_expr": "and",
        "conditions": [
            {
                "code": 1,
                "entity_id": condition_device.device_id,
                "entity_type": "device_report",
                "expr": {
                    "status_code": status_code,
                    "comparator": comparator,
                    "status_value": threshold,
                },
            }
        ],
        "actions": [
            {
                "entity_id": target_device.device_id,
                "action_executor": "device_issue",
                "executor_property": {
                    "function_code": switch_code,
                    "function_value": action_state,
                },
            }
        ],
    }

    result = create_and_enable_automation(payload, confirm=True, enable=True)
    rule_id = result.get("rule_id") or result.get("id")
    if rule_id:
        payload["rule_id"] = rule_id

    return _build_success_message(
        action_on=action_state,
        device_name=target_device.friendly_name,
        comparator=comparator,
        threshold=threshold,
        rule_name=rule_name,
    )


async def try_handle_fastpath(user_input: str) -> Optional[str]:
    if not _FASTPATH_ENABLED:
        return None

    normalized_input = _normalize_text(user_input)
    if not _has_fastpath_keywords(normalized_input):
        return None

    try:
        return await asyncio.to_thread(_process_fastpath_sync, user_input, normalized_input)
    except Exception as exc:  # pragma: no cover - operational failure
        print(f"⚠️ Alexa fastpath failed: {exc}")
        return None
