"""High-level orchestration for Tuya automation workflows."""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

from integrations.tuya import TuyaClient
from integrations.tuya.models import DeviceLite, Property, SceneRule

from .heuristics import HeuristicContext, HeuristicProposal, build_heuristic_proposals

LOGGER = logging.getLogger(__name__)


def load_automation_config(path: str | pathlib.Path | None) -> Dict[str, Any]:
    """Read automation config YAML, returning an empty dict if file is missing."""
    if path is None:
        return {}
    config_path = pathlib.Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Automation config not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class TuyaAutomationWorkflow:
    """Coordinates device discovery, property harvesting, and scene creation."""

    def __init__(self, client: TuyaClient) -> None:
        self._client = client

    def discover_devices(
        self,
        space_ids: Iterable[str],
        *,
        is_recursion: bool = False,
        page_size: int = 20,
        last_id: Optional[str] = None,
    ) -> List[DeviceLite]:
        space_ids_list = list(space_ids)
        LOGGER.debug("Fetching devices for spaces: %s", space_ids_list)
        payload = self._client.list_space_devices(
            space_ids_list,
            is_recursion=is_recursion,
            page_size=page_size,
            last_id=last_id,
        )
        devices = [DeviceLite.model_validate(device) for device in payload]
        return devices

    def build_device_map(self, devices: Sequence[DeviceLite]) -> Dict[str, DeviceLite]:
        return {device.id: device for device in devices}

    def inspect_properties(
        self,
        device_ids: Iterable[str],
        *,
        codes: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Property]]:
        result: Dict[str, Dict[str, Property]] = {}
        for device_id in device_ids:
            LOGGER.debug("Fetching shadow properties for device %s", device_id)
            shadow = self._client.get_device_shadow(device_id, codes=codes)
            properties_payload = shadow.get("properties", [])
            props = [Property.model_validate(prop) for prop in properties_payload]
            result[device_id] = {prop.code: prop for prop in props}
        return result

    def propose_scene_rules(
        self,
        *,
        space_id: str,
        devices: Dict[str, DeviceLite],
        properties: Dict[str, Dict[str, Property]],
        config: Dict[str, Any],
        heuristics: Optional[Iterable[str]] = None,
    ) -> List[HeuristicProposal]:
        enabled_heuristics = list(heuristics) if heuristics else config.get("enabled_heuristics") or []
        if not enabled_heuristics:
            enabled_heuristics = list(config.get("heuristics", {}).keys())

        context = HeuristicContext(
            space_id=space_id,
            devices=devices,
            properties=properties,
            config=config,
        )

        return build_heuristic_proposals(context, enabled_heuristics)

    def build_scene_payloads(
        self,
        *,
        space_id: str,
        proposals: Sequence[HeuristicProposal],
    ) -> List[Dict[str, Any]]:
        payloads = []
        for proposal in proposals:
            rule = proposal.to_scene_rule(space_id)
            payloads.append(rule.as_payload())
        return payloads

    def create_scenes(
        self,
        payloads: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for payload in payloads:
            LOGGER.info("Creating scene: %s", payload.get("name"))
            result = self._client.create_scene(payload)
            results.append(result)
        return results

    def list_scenes(self, space_id: str) -> List[Dict[str, Any]]:
        return self._client.list_scenes(space_id)

    def get_scene(self, rule_id: str) -> Dict[str, Any]:
        return self._client.get_scene(rule_id)

    def enable_scene(self, rule_id: str, *, enable: bool = True) -> Dict[str, Any]:
        return self._client.set_scene_state(rule_id, enable)

    def trigger_scene(self, rule_id: str) -> Dict[str, Any]:
        return self._client.trigger_scene(rule_id)

    def update_scene(self, rule_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._client.update_scene(rule_id, payload)

    def delete_scenes(self, ids: Sequence[str], space_id: str) -> Dict[str, Any]:
        return self._client.delete_scenes(ids, space_id)

    def set_scenes_state(self, ids: Sequence[str], is_enable: bool) -> Dict[str, Any]:
        return self._client.set_scenes_state(ids, is_enable)

    @staticmethod
    def serialize_payload(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, indent=2, ensure_ascii=False)
