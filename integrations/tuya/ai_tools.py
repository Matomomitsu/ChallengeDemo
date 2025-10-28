"""AI-facing helpers for orchestrating Tuya automation workflows safely."""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional
from threading import Lock

from dotenv import load_dotenv

from integrations.tuya import TuyaApiError, TuyaClient
from integrations.tuya.client import DEFAULT_TUYA_API_BASE_URL
from integrations.tuya.workflow import TuyaAutomationWorkflow, load_automation_config
from core.tuya_scene_builder import (
    SceneBuilderError,
    build_scene_payload as ai_build_scene_payload,
)

_REDACT_KEYS = {"client_secret", "access_token", "localKey"}
_SCENE_CONTEXT_TTL_SECONDS = int(os.getenv("TUYA_SCENE_CONTEXT_TTL", "60"))
_SCENE_BUILDER_CACHE: Dict[str, Dict[str, Any]] = {}
_TUYA_CLIENT_LOCK = Lock()
_SHARED_TUYA_CLIENT: Optional[TuyaClient] = None
_SHARED_WORKFLOW: Optional[TuyaAutomationWorkflow] = None


def _redact(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: ("***" if k in _REDACT_KEYS else _redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact(item) for item in data]
    return data


def _wrap_result(result: Any, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(result, dict):
        payload: Dict[str, Any] = dict(result)
    elif isinstance(result, bool):
        payload = {"success": result}
    elif result is None:
        payload = {"success": True}
    else:
        payload = {"result": result}
    if extra:
        payload.update(extra)
    return _redact(payload)


def _humanize_code(code: str) -> str:
    if not code:
        return ""
    label = re.sub(r"[_\-]+", " ", code).strip()
    if not label:
        label = code
    if label.isupper():
        label = label.title()
    else:
        label = label[:1].upper() + label[1:]
    return label.replace(" Id", " ID")


def _property_display(code: str, prop: Any) -> Dict[str, Any]:
    label = getattr(prop, "custom_name", None) or _humanize_code(code)
    display: Dict[str, Any] = {
        "code": code,
        "label": label,
        "value": getattr(prop, "value", None),
    }
    dp_id = getattr(prop, "dp_id", None)
    if dp_id is not None:
        display["dp_id"] = dp_id
    prop_type = getattr(prop, "type", None)
    if prop_type:
        display["type"] = prop_type
    timestamp = getattr(prop, "time", None)
    if timestamp is not None:
        display["updated_at"] = timestamp
    return display


def _normalize_comparator_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        comparator_map = {
            ">": ">",
            "gt": ">",
            "greater": ">",
            "greater_than": ">",
            "maior": ">",
            "acima": ">",
            "above": ">",
            "<": "<",
            "lt": "<",
            "less": "<",
            "less_than": "<",
            "below": "<",
            "under": "<",
            "menor": "<",
            "abaixo": "<",
            "=": "==",
            "==": "==",
            "equals": "==",
            "igual": "==",
        }
        direct = comparator_map.get(text)
        if direct:
            return direct
        if "<" in text:
            return "<"
        if ">" in text:
            return ">"
        if "=" in text:
            return "=="
        return None
    if value in {">", "<", "=="}:
        return value
    return None


def _coerce_switch_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        truthy = {"on", "true", "1", "ligar", "ativar", "enable", "enabled", "yes", "sim"}
        falsy = {"off", "false", "0", "desligar", "apagar", "disable", "disabled", "no", "nao", "não"}
        if text in truthy:
            return True
        if text in falsy:
            return False
    return None


def _normalize_heuristic_params(params: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(params)
    threshold_aliases = [
        normalized.pop("soc_threshold", None),
        normalized.get("threshold_percent"),
    ]
    threshold_value = next((value for value in threshold_aliases if value is not None), None)
    if threshold_value is not None:
        normalized.setdefault("threshold", threshold_value)
        normalized.setdefault("status_value", threshold_value)
        normalized.pop("threshold_percent", None)

    if "threshold" in normalized and "pv_threshold_w" not in normalized:
        normalized["pv_threshold_w"] = normalized["threshold"]

    pv_threshold_alias_keys = [
        "pv_threshold",
        "surplus_threshold",
        "surplus_threshold_w",
        "surplus_limit",
        "surplus_limit_w",
        "excess_threshold",
        "excess_threshold_w",
    ]
    for key in pv_threshold_alias_keys:
        value = normalized.pop(key, None)
        if value is not None and "pv_threshold_w" not in normalized:
            normalized["pv_threshold_w"] = value

    if "comparator" in normalized:
        comparator_value = _normalize_comparator_value(normalized["comparator"])
        if comparator_value:
            normalized["comparator"] = comparator_value
    else:
        comparator_candidates = [
            normalized.pop("comparison", None),
            normalized.pop("operator", None),
            normalized.pop("comparison_operator", None),
            normalized.pop("comparador", None),
            normalized.pop("direction", None),
        ]
        for candidate in comparator_candidates:
            comparator_value = _normalize_comparator_value(candidate)
            if comparator_value:
                normalized["comparator"] = comparator_value
                break

    if "switch_value" in normalized:
        coerced = _coerce_switch_value(normalized["switch_value"])
        if coerced is not None:
            normalized["switch_value"] = coerced
    else:
        switch_candidates = [
            normalized.pop("switch_state", None),
            normalized.pop("power_state", None),
            normalized.pop("state", None),
            normalized.pop("desired_state", None),
            normalized.pop("acao", None),
            normalized.pop("action", None),
        ]
        turn_on = normalized.pop("turn_on", None)
        if turn_on is not None:
            coerced = _coerce_switch_value(turn_on)
            if coerced is None and bool(turn_on):
                coerced = True
            if coerced is True:
                switch_candidates.append(True)
        turn_off = normalized.pop("turn_off", None)
        if turn_off is not None:
            coerced = _coerce_switch_value(turn_off)
            if coerced is None and bool(turn_off):
                coerced = False
            if coerced is False:
                switch_candidates.append(False)
        for candidate in switch_candidates:
            coerced = _coerce_switch_value(candidate)
            if coerced is not None:
                normalized["switch_value"] = coerced
                break

    load_switch_aliases = [
        normalized.pop("load_dp_code", None),
        normalized.pop("switch_dp_code", None),
        normalized.pop("switch_dp", None),
    ]
    switch_code = next((value for value in load_switch_aliases if value is not None), None)
    if switch_code:
        function_codes = dict(normalized.get("function_codes") or {})
        function_codes.setdefault("switch", switch_code)
        normalized["function_codes"] = function_codes
    return normalized


def _get_shared_workflow() -> TuyaAutomationWorkflow:
    global _SHARED_TUYA_CLIENT, _SHARED_WORKFLOW
    if _SHARED_WORKFLOW is not None:
        return _SHARED_WORKFLOW

    with _TUYA_CLIENT_LOCK:
        if _SHARED_WORKFLOW is not None:
            return _SHARED_WORKFLOW

        load_dotenv(".env")
        client_id = os.getenv("TUYA_CLIENT_ID")
        client_secret = os.getenv("TUYA_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError("Tuya credentials not configured")

        base_url = os.getenv("TUYA_API_BASE_URL", DEFAULT_TUYA_API_BASE_URL)
        _SHARED_TUYA_CLIENT = TuyaClient(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
        )
        _SHARED_WORKFLOW = TuyaAutomationWorkflow(_SHARED_TUYA_CLIENT)
        return _SHARED_WORKFLOW


def _build_workflow(config_path: Optional[str] = None) -> tuple[TuyaAutomationWorkflow, Dict[str, Any]]:
    workflow = _get_shared_workflow()
    config = load_automation_config(config_path) if config_path else {}
    return workflow, config


def prewarm_tuya_caches(space_id: Optional[str]) -> None:
    if not space_id:
        return
    try:
        workflow = _get_shared_workflow()
        _get_scene_builder_context(workflow, space_id)
    except Exception as exc:  # pragma: no cover - network issues
        print(f"⚠️ Could not prewarm Tuya caches for space {space_id}: {exc}")


def _get_scene_builder_context(
    workflow: TuyaAutomationWorkflow,
    space_id: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    now = time.monotonic()
    cache_entry = _SCENE_BUILDER_CACHE.get(space_id)
    if cache_entry and (now - cache_entry.get("timestamp", 0.0)) < _SCENE_CONTEXT_TTL_SECONDS:
        return cache_entry["devices"], cache_entry["properties"], cache_entry["scenes"]

    devices = workflow.discover_devices([space_id])
    device_map = workflow.build_device_map(devices)
    properties = workflow.inspect_properties(device_map.keys())
    scenes = workflow.list_scenes(space_id)

    devices_payload = [
        {
            "id": device.id,
            "friendly_name": (device.customName or device.name or "").strip() or "Dispositivo",
            "category": device.category,
        }
        for device in devices
    ]
    properties_payload = {
        device_id: {code: prop.value for code, prop in props.items()} for device_id, props in properties.items()
    }
    scenes_payload = [
        {
            "id": scene.get("rule_id") or scene.get("id"),
            "name": scene.get("name") or scene.get("display_name"),
            "type": scene.get("type"),
        }
        for scene in scenes
    ]

    _SCENE_BUILDER_CACHE[space_id] = {
        "timestamp": now,
        "devices": devices_payload,
        "properties": properties_payload,
        "scenes": scenes_payload,
    }
    return devices_payload, properties_payload, scenes_payload


def describe_space(space_id: str, *, config_path: Optional[str] = None) -> Dict[str, Any]:
    """Return devices and scenes for the space (token-safe)."""
    workflow, _ = _build_workflow(config_path)
    devices = workflow.discover_devices([space_id])
    scenes = workflow.list_scenes(space_id)
    return {
        "space_id": space_id,
        "devices": [_redact(device.model_dump(exclude_none=True)) for device in devices],
        "scenes": _redact(scenes),
    }


def inspect_device(device_id: str, *, codes: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    workflow, _ = _build_workflow()
    props_map = workflow.inspect_properties([device_id], codes=list(codes) if codes else None)
    prop_objects = props_map.get(device_id, {})
    properties = {code: prop.model_dump(exclude_none=True) for code, prop in prop_objects.items()}
    displays = [_property_display(code, prop) for code, prop in sorted(prop_objects.items())]
    return {
        "device_id": device_id,
        "properties": _redact(properties),
        "properties_display": displays,
    }


def propose_automation(
    space_id: str,
    heuristic_set: Optional[Iterable[str]] = None,
    *,
    config_path: Optional[str] = None,
    heuristic_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    workflow, config = _build_workflow(config_path)
    heuristics_cfg = config.setdefault("heuristics", {})
    if heuristic_overrides:
        for key, params in heuristic_overrides.items():
            existing = heuristics_cfg.get(key, {}) or {}
            merged = dict(existing)
            merged.update(_normalize_heuristic_params(params))
            heuristics_cfg[key] = merged
    heuristic_keys = list(heuristic_set) if heuristic_set else None
    if heuristic_keys is None and heuristic_overrides:
        heuristic_keys = list(heuristic_overrides.keys())
    if heuristic_keys is None:
        heuristic_keys = list(heuristics_cfg.keys()) or None
    proposals, device_map, properties = _prepare_proposals(workflow, config, space_id, heuristic_keys)
    payloads = workflow.build_scene_payloads(space_id=space_id, proposals=proposals)
    return {
        "space_id": space_id,
        "heuristics": heuristic_keys if heuristic_keys else list(config.get("heuristics", {}).keys()),
        "payloads": [_redact(payload) for payload in payloads],
        "devices": {device_id: device.model_dump(exclude_none=True) for device_id, device in device_map.items()},
        "properties": {device_id: {code: prop.value for code, prop in values.items()} for device_id, values in properties.items()},
    }


def build_scene_payload_from_instructions(
    *,
    instructions: str,
    space_id: Optional[str] = None,
    name_hint: Optional[str] = None,
    decision_expr_hint: Optional[str] = None,
    effective_time_hint: Optional[Dict[str, Any]] = None,
    type_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Leverage the Gemini builder to construct a payload for POST /v2.0/cloud/scene/rule."""
    if not instructions or not instructions.strip():
        raise ValueError("instructions must be provided")

    workflow, _ = _build_workflow()
    resolved_space = space_id or os.getenv("TUYA_SPACE_ID")
    if not resolved_space:
        raise RuntimeError("space_id must be provided or TUYA_SPACE_ID must be set")

    device_context, properties_context, scenes_context = _get_scene_builder_context(workflow, resolved_space)

    hints: Dict[str, Any] = {}
    if name_hint:
        hints["name"] = name_hint
    if decision_expr_hint:
        hints["decision_expr"] = decision_expr_hint
    if effective_time_hint:
        hints["effective_time"] = effective_time_hint
    if type_hint:
        hints["type"] = type_hint

    try:
        builder_result = ai_build_scene_payload(
            instructions=instructions,
            context={
                "space_id": resolved_space,
                "devices": device_context,
                "properties": properties_context,
                "scenes": scenes_context,
                "hints": hints,
            },
        )
    except SceneBuilderError as exc:
        raise RuntimeError(f"Failed to generate scene payload: {exc}") from exc

    payload: Dict[str, Any] = dict(builder_result.get("payload") or {})
    if not payload:
        return {
            "space_id": resolved_space,
            "payload": {},
            "summary": "Scene builder returned an empty payload.",
        }

    payload.setdefault("space_id", resolved_space)
    if name_hint and "name" not in payload:
        payload["name"] = name_hint
    if decision_expr_hint and "decision_expr" not in payload:
        payload["decision_expr"] = decision_expr_hint
    if effective_time_hint and "effective_time" not in payload:
        payload["effective_time"] = effective_time_hint
    if type_hint and "type" not in payload:
        payload["type"] = type_hint

    # Validate JSON serialisation (raises if not serialisable)
    workflow.serialize_payload(payload)

    summary = {
        "instructions": instructions,
        "devices_considered": [
            {"friendly_name": device["friendly_name"], "category": device["category"]}
            for device in device_context
        ],
    }

    return {
        "space_id": resolved_space,
        "payload": payload,
        "summary": summary,
    }


def create_and_enable_automation(
    payload: Dict[str, Any],
    *,
    confirm: bool,
    enable: bool = True,
) -> Dict[str, Any]:
    if not confirm:
        raise PermissionError("Creation requires explicit confirmation")
    workflow, _ = _build_workflow()
    try:
        result = workflow.create_scenes([payload])[0]
    except TuyaApiError as exc:
        raise RuntimeError(f"Tuya error creating scene: {exc}") from exc

    rule_id = result.get("rule_id") or result.get("id")
    if enable and rule_id:
        payload_type = (payload.get("type") or "").strip().lower()
        if payload_type in {"scene", "tap-to-run", "tap_to_run"}:
            enable = False
        else:
            try:
                workflow.enable_scene(rule_id, enable=True)
            except TuyaApiError as exc:
                raise RuntimeError(f"Scene created but enabling failed: {exc}") from exc
    return _wrap_result(result, extra={"rule_id": rule_id, "enabled": bool(enable)})


def trigger_scene(rule_id: str, *, confirm: bool) -> Dict[str, Any]:
    if not confirm:
        raise PermissionError("Triggering scenes requires confirmation")
    workflow, _ = _build_workflow()
    try:
        result = workflow.trigger_scene(rule_id)
    except TuyaApiError as exc:
        raise RuntimeError(f"Failed to trigger scene: {exc}") from exc
    return _wrap_result(result, extra={"rule_id": rule_id})


def update_automation(
    rule_id: str,
    payload: Dict[str, Any],
    *,
    confirm: bool,
) -> Dict[str, Any]:
    if not confirm:
        raise PermissionError("Updating scenes requires confirmation")
    workflow, _ = _build_workflow()
    try:
        result = workflow.update_scene(rule_id, payload)
    except (TuyaApiError, ValueError) as exc:
        raise RuntimeError(f"Failed to update scene: {exc}") from exc
    return _wrap_result(result, extra={"rule_id": rule_id})


def delete_automations(
    rule_ids: Iterable[str],
    *,
    space_id: Optional[str] = None,
    confirm: bool,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    if not confirm:
        raise PermissionError("Deleting scenes requires confirmation")
    workflow, config = _build_workflow(config_path)
    resolved_space = space_id or config.get("space_id") or os.getenv("TUYA_SPACE_ID")
    if not resolved_space:
        raise RuntimeError("space_id must be provided via argument, config, or TUYA_SPACE_ID")
    rule_ids_list = list(rule_ids)
    try:
        result = workflow.delete_scenes(rule_ids_list, resolved_space)
    except (TuyaApiError, ValueError) as exc:
        raise RuntimeError(f"Failed to delete scenes: {exc}") from exc
    return _wrap_result(result, extra={"rule_ids": rule_ids_list, "space_id": resolved_space})


def set_automation_state(
    rule_ids: Iterable[str],
    *,
    enable: bool,
    confirm: bool,
) -> Dict[str, Any]:
    if not confirm:
        raise PermissionError("Updating scene state requires confirmation")
    workflow, _ = _build_workflow()
    rule_ids_list = list(rule_ids)
    try:
        result = workflow.set_scenes_state(rule_ids_list, enable)
    except (TuyaApiError, ValueError) as exc:
        raise RuntimeError(f"Failed to update scene state: {exc}") from exc
    return _wrap_result(result, extra={"rule_ids": rule_ids_list, "enable": enable})


def _prepare_proposals(
    workflow: TuyaAutomationWorkflow,
    config: Dict[str, Any],
    space_id: str,
    heuristic_set: Optional[Iterable[str]],
):
    devices = workflow.discover_devices([space_id])
    device_map = workflow.build_device_map(devices)
    device_ids = set()
    for params in (config.get("heuristics") or {}).values():
        for key in ("inverter_device_id", "load_device_id", "sensor_device_id"):
            value = params.get(key)
            if value:
                device_ids.add(value)
    if not device_ids:
        device_ids = set(device_map.keys())
    properties = workflow.inspect_properties(device_ids)
    proposals = workflow.propose_scene_rules(
        space_id=space_id,
        devices=device_map,
        properties=properties,
        config=config,
        heuristics=list(heuristic_set) if heuristic_set else None,
    )
    return proposals, device_map, properties


def propose_generic_scene(
    *,
    space_id: str,
    name: str,
    conditions: List[Dict[str, Any]],
    actions: List[Dict[str, Any]],
    decision_expr: str = "and",
    effective_time: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a single generic automation scene payload from explicit condition/action specs.

    Conditions items:
      - entity_id (str), status_code (str), comparator (>,<,== or synonyms), status_value (Any), code (int, optional)
    Actions items:
      - entity_id (str), function_code (str), function_value (Any)
    """
    load_dotenv(".env")
    workflow, _ = _build_workflow()

    def _norm_condition(item: Dict[str, Any]) -> Dict[str, Any]:
        comparator = _normalize_comparator_value(item.get("comparator")) or ">"
        return {
            "entity_id": item["entity_id"],
            "entity_type": "device_report",
            "expr": {
                "status_code": item["status_code"],
                "comparator": comparator,
                "status_value": item.get("status_value"),
            },
            **({"code": int(item["code"])} if item.get("code") is not None else {}),
        }

    def _norm_action(item: Dict[str, Any]) -> Dict[str, Any]:
        value = item.get("function_value")
        if value is None:
            # aliases
            value = item.get("value")
            if value is None:
                value = item.get("state")
        value = _coerce_switch_value(value)
        return {
            "entity_id": item["entity_id"],
            "action_executor": "device_issue",
            "executor_property": {
                "function_code": item["function_code"],
                "function_value": value,
            },
        }

    payload = {
        "space_id": space_id,
        "name": name,
        "type": "automation",
        "decision_expr": decision_expr,
        "conditions": [_norm_condition(it) for it in conditions],
        "actions": [_norm_action(it) for it in actions],
    }
    if effective_time:
        payload["effective_time"] = effective_time
    # Validate by round-tripping via API serializer (no network)
    workflow.serialize_payload(payload)
    return {
        "space_id": space_id,
        "payload": _redact(payload),
    }


def create_and_enable_generic_scene(
    *,
    payload: Dict[str, Any],
    confirm: bool,
    enable: bool = True,
) -> Dict[str, Any]:
    if not confirm:
        raise PermissionError("Creation requires explicit confirmation")
    workflow, _ = _build_workflow()
    try:
        result = workflow.create_scenes([payload])[0]
    except TuyaApiError as exc:
        raise RuntimeError(f"Tuya error creating scene: {exc}") from exc

    rule_id = result.get("rule_id") or result.get("id")
    if enable and rule_id:
        try:
            workflow.enable_scene(rule_id, enable=True)
        except TuyaApiError as exc:
            raise RuntimeError(f"Scene created but enabling failed: {exc}") from exc
    return _wrap_result(result, extra={"rule_id": rule_id, "enabled": bool(enable)})
