"""AI-facing helpers for orchestrating Tuya automation workflows safely."""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv

from integrations.tuya import TuyaApiError, TuyaClient
from integrations.tuya.client import DEFAULT_TUYA_API_BASE_URL
from integrations.tuya.workflow import TuyaAutomationWorkflow, load_automation_config

_REDACT_KEYS = {"client_secret", "access_token", "localKey"}


def _redact(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: ("***" if k in _REDACT_KEYS else _redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact(item) for item in data]
    return data


def _build_workflow(config_path: Optional[str] = None) -> tuple[TuyaAutomationWorkflow, Dict[str, Any]]:
    load_dotenv(".env")
    client_id = os.getenv("TUYA_CLIENT_ID")
    client_secret = os.getenv("TUYA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Tuya credentials not configured")

    base_url = os.getenv("TUYA_API_BASE_URL", DEFAULT_TUYA_API_BASE_URL)
    workflow = TuyaAutomationWorkflow(TuyaClient(client_id=client_id, client_secret=client_secret, base_url=base_url))
    config = load_automation_config(config_path) if config_path else {}
    return workflow, config


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
    properties = {code: prop.model_dump(exclude_none=True) for code, prop in props_map.get(device_id, {}).items()}
    return {"device_id": device_id, "properties": _redact(properties)}


def propose_automation(
    space_id: str,
    heuristic_set: Optional[Iterable[str]] = None,
    *,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    workflow, config = _build_workflow(config_path)
    heuristic_keys = list(heuristic_set) if heuristic_set else None
    proposals, device_map, properties = _prepare_proposals(workflow, config, space_id, heuristic_keys)
    payloads = workflow.build_scene_payloads(space_id=space_id, proposals=proposals)
    return {
        "space_id": space_id,
        "heuristics": heuristic_keys if heuristic_keys else list(config.get("heuristics", {}).keys()),
        "payloads": [_redact(payload) for payload in payloads],
        "devices": {device_id: device.model_dump(exclude_none=True) for device_id, device in device_map.items()},
        "properties": {device_id: {code: prop.value for code, prop in values.items()} for device_id, values in properties.items()},
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
        try:
            workflow.enable_scene(rule_id, enable=True)
        except TuyaApiError as exc:
            raise RuntimeError(f"Scene created but enabling failed: {exc}") from exc
    return _redact(result)


def trigger_scene(rule_id: str, *, confirm: bool) -> Dict[str, Any]:
    if not confirm:
        raise PermissionError("Triggering scenes requires confirmation")
    workflow, _ = _build_workflow()
    try:
        result = workflow.trigger_scene(rule_id)
    except TuyaApiError as exc:
        raise RuntimeError(f"Failed to trigger scene: {exc}") from exc
    return _redact(result)


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
    return _redact(result)


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
    try:
        result = workflow.delete_scenes(list(rule_ids), resolved_space)
    except (TuyaApiError, ValueError) as exc:
        raise RuntimeError(f"Failed to delete scenes: {exc}") from exc
    return _redact(result)


def set_automation_state(
    rule_ids: Iterable[str],
    *,
    enable: bool,
    confirm: bool,
) -> Dict[str, Any]:
    if not confirm:
        raise PermissionError("Updating scene state requires confirmation")
    workflow, _ = _build_workflow()
    try:
        result = workflow.set_scenes_state(list(rule_ids), enable)
    except (TuyaApiError, ValueError) as exc:
        raise RuntimeError(f"Failed to update scene state: {exc}") from exc
    return _redact(result)


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
