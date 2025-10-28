"""Heuristic builders for Tuya automation scenes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from integrations.tuya.mapping import resolve_function_code, resolve_property_code
from integrations.tuya.models import (
    ConditionExpr,
    DeviceLite,
    EffectiveTime,
    ExecutorProperty,
    Property,
    SceneAction,
    SceneCondition,
    SceneRule,
)


@dataclass
class HeuristicContext:
    space_id: str
    devices: Dict[str, DeviceLite]
    properties: Dict[str, Dict[str, Property]]
    config: Dict[str, Any]


@dataclass
class HeuristicProposal:
    key: str
    name: str
    conditions: List[SceneCondition]
    actions: List[SceneAction]
    decision_expr: str = "and"
    effective_time: Optional[EffectiveTime] = None

    def to_scene_rule(self, space_id: str) -> SceneRule:
        numbered_conditions: List[SceneCondition] = []
        for index, condition in enumerate(self.conditions, start=1):
            if condition.code is not None:
                numbered_conditions.append(condition)
            else:
                numbered_conditions.append(
                    condition.model_copy(update={"code": index})
                )
        return SceneRule(
            space_id=space_id,
            name=self.name,
            type="automation",
            decision_expr=self.decision_expr,
            conditions=numbered_conditions,
            actions=self.actions,
            effective_time=self.effective_time,
        )


def _ensure_device(context: HeuristicContext, device_id: str, role: str) -> Any:
    if device_id not in context.devices:
        raise ValueError(f"Unknown {role} device_id '{device_id}' for heuristic")
    return context.devices[device_id]


def _resolve_property_code(
    device: Any,
    logical_key: str,
    overrides: Optional[Dict[str, str]],
    available: Dict[str, Any],
) -> str:
    override_mapping = overrides or {}
    code = resolve_property_code(device, logical_key, override_mapping)
    if code and code in available:
        return code

    # Fall back to explicit override even if not in registry
    if logical_key in override_mapping:
        code = override_mapping[logical_key]
        if code not in available:
            raise ValueError(f"Property code '{code}' not in device shadow for {device.id}")
        return code

    raise ValueError(f"Unable to resolve property code for logical key '{logical_key}' on device {device.id}")


def _resolve_function_code(
    device: Any,
    logical_key: str,
    overrides: Optional[Dict[str, str]],
    available: Optional[Dict[str, Property]] = None,
) -> str:
    override_mapping = overrides or {}
    code = resolve_function_code(device, logical_key, override_mapping)
    if code:
        return code
    if logical_key in override_mapping:
        return override_mapping[logical_key]
    if available:
        if logical_key == "switch":
            for candidate in ("switch", "switch_1", "switch_led"):
                if candidate in available:
                    return candidate
            for prop in available.values():
                prop_code = getattr(prop, "code", None)
                if prop_code and prop_code.startswith("switch"):
                    return prop_code
    raise ValueError(f"Unable to resolve function code for logical key '{logical_key}' on device {device.id}")


def _build_condition(
    *,
    entity_id: str,
    status_code: str,
    comparator: str,
    status_value: Any,
    code_index: Optional[int] = None,
) -> SceneCondition:
    return SceneCondition(
        entity_id=entity_id,
        entity_type="device_report",
        code=code_index,
        expr=ConditionExpr(
            status_code=status_code,
            comparator=comparator,
            status_value=status_value,
        ),
    )


def _build_action(
    *,
    entity_id: str,
    function_code: str,
    function_value: Any,
) -> SceneAction:
    return SceneAction(
        entity_id=entity_id,
        action_executor="device_issue",
        executor_property=ExecutorProperty(
            function_code=function_code,
            function_value=function_value,
        ),
    )


def _coerce_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"on", "true", "1", "ligar", "ativar", "enable", "enabled", "yes", "sim"}:
            return True
        if text in {"off", "false", "0", "desligar", "apagar", "disable", "disabled", "no", "nao", "não"}:
            return False
    return value


def _build_extra_actions(
    context: HeuristicContext,
    items: Optional[List[Dict[str, Any]]],
    function_overrides: Optional[Dict[str, str]] = None,
) -> List[SceneAction]:
    actions: List[SceneAction] = []
    if not items:
        return actions
    for item in items:
        if not isinstance(item, dict):
            continue
        entity_id = item.get("entity_id")
        if not entity_id:
            continue
        device = _ensure_device(context, entity_id, "action target")
        function_code = item.get("function_code")
        function = item.get("function")
        if not function_code and function:
            if function == "switch":
                available = context.properties.get(entity_id, {})
                function_code = _resolve_function_code(device, "switch", function_overrides, available)
        if not function_code and function == "switch":
            available = context.properties.get(entity_id, {})
            function_code = _resolve_function_code(device, "switch", function_overrides, available)
        if not function_code:
            # Allow direct DP code if provided as 'code'
            function_code = item.get("code")
        if not function_code:
            # Unable to construct this action
            continue
        function_value = item.get("function_value")
        if function_value is None:
            # Try common aliases
            function_value = item.get("value")
        if function_value is None:
            function_value = item.get("state")
        function_value = _coerce_bool(function_value)
        # Default to True if still None
        if function_value is None:
            function_value = True
        actions.append(_build_action(entity_id=entity_id, function_code=function_code, function_value=function_value))
    return actions


def heuristic_battery_protect(context: HeuristicContext, params: Dict[str, Any]) -> HeuristicProposal:
    inverter_device_id = params.get("inverter_device_id")
    load_device_id = params.get("load_device_id")
    if not inverter_device_id or not load_device_id:
        raise ValueError("battery_protect heuristic requires inverter_device_id and load_device_id")

    inverter_device = _ensure_device(context, inverter_device_id, "inverter")
    load_device = _ensure_device(context, load_device_id, "load")

    threshold = params.get("threshold", 90)
    property_overrides = params.get("property_codes")
    function_overrides = params.get("function_codes")

    inverter_properties = context.properties.get(inverter_device_id, {})
    battery_code = _resolve_property_code(
        inverter_device,
        "battery_soc",
        property_overrides,
        inverter_properties,
    )

    load_properties = context.properties.get(load_device_id, {})
    switch_code = _resolve_function_code(load_device, "switch", function_overrides, load_properties)

    condition = _build_condition(
        entity_id=inverter_device_id,
        status_code=battery_code,
        comparator=params.get("comparator", "<"),
        status_value=params.get("status_value", threshold),
    )
    action = _build_action(
        entity_id=load_device_id,
        function_code=switch_code,
        function_value=params.get("switch_value", False),
    )

    return HeuristicProposal(
        key="battery_protect",
        name=params.get("name", "Battery Protect"),
        conditions=[condition],
        actions=[action],
        decision_expr=params.get("decision_expr", "and"),
    )


def heuristic_battery_surplus(context: HeuristicContext, params: Dict[str, Any]) -> HeuristicProposal:
    inverter_device_id = params.get("inverter_device_id")
    load_device_id = params.get("load_device_id")
    if not inverter_device_id or not load_device_id:
        raise ValueError("battery_surplus heuristic requires inverter_device_id and load_device_id")

    inverter_device = _ensure_device(context, inverter_device_id, "inverter")
    load_device = _ensure_device(context, load_device_id, "load")

    threshold = params.get("threshold", 90)
    property_overrides = params.get("property_codes")
    function_overrides = params.get("function_codes")

    inverter_properties = context.properties.get(inverter_device_id, {})
    battery_code = _resolve_property_code(
        inverter_device,
        "battery_soc",
        property_overrides,
        inverter_properties,
    )

    load_properties = context.properties.get(load_device_id, {})
    switch_code = _resolve_function_code(load_device, "switch", function_overrides, load_properties)

    comparator = params.get("comparator", ">")
    switch_value = params.get("switch_value")
    if switch_value is None:
        switch_value = True

    condition = _build_condition(
        entity_id=inverter_device_id,
        status_code=battery_code,
        comparator=comparator,
        status_value=params.get("status_value", threshold),
    )
    actions: List[SceneAction] = [
        _build_action(
            entity_id=load_device_id,
            function_code=switch_code,
            function_value=switch_value,
        )
    ]
    actions.extend(_build_extra_actions(context, params.get("extra_actions"), function_overrides))

    return HeuristicProposal(
        key="battery_surplus",
        name=params.get("name", "Battery Surplus"),
        conditions=[condition],
        actions=actions,
        decision_expr=params.get("decision_expr", "and"),
    )


def heuristic_solar_surplus(context: HeuristicContext, params: Dict[str, Any]) -> HeuristicProposal:
    inverter_device_id = params.get("inverter_device_id")
    load_device_id = params.get("load_device_id")
    if not inverter_device_id or not load_device_id:
        raise ValueError("solar_surplus heuristic requires inverter_device_id and load_device_id")

    inverter_device = _ensure_device(context, inverter_device_id, "inverter")
    load_device = _ensure_device(context, load_device_id, "load")

    property_overrides = params.get("property_codes")
    function_overrides = params.get("function_codes")

    inverter_properties = context.properties.get(inverter_device_id, {})
    pv_code = _resolve_property_code(
        inverter_device,
        "pv_power",
        property_overrides,
        inverter_properties,
    )
    load_properties = context.properties.get(load_device_id, {})
    switch_code = _resolve_function_code(load_device, "switch", function_overrides, load_properties)

    threshold = params.get("pv_threshold_w")
    if threshold is None:
        threshold = params.get("threshold")
    if threshold is None:
        threshold = params.get("status_value")
    if threshold is None:
        threshold = params.get("pv_threshold")
    if threshold is None:
        threshold = 800

    condition = _build_condition(
        entity_id=inverter_device_id,
        status_code=pv_code,
        comparator=params.get("comparator", ">"),
        status_value=threshold,
    )
    actions: List[SceneAction] = [
        _build_action(
            entity_id=load_device_id,
            function_code=switch_code,
            function_value=params.get("switch_value", True),
        )
    ]
    actions.extend(_build_extra_actions(context, params.get("extra_actions"), function_overrides))

    return HeuristicProposal(
        key="solar_surplus",
        name=params.get("name", "Solar Surplus"),
        conditions=[condition],
        actions=actions,
        decision_expr=params.get("decision_expr", "and"),
    )


def heuristic_solar_deficit(context: HeuristicContext, params: Dict[str, Any]) -> HeuristicProposal:
    inverter_device_id = params.get("inverter_device_id")
    load_device_id = params.get("load_device_id")
    if not inverter_device_id or not load_device_id:
        raise ValueError("solar_deficit heuristic requires inverter_device_id and load_device_id")

    inverter_device = _ensure_device(context, inverter_device_id, "inverter")
    load_device = _ensure_device(context, load_device_id, "load")

    property_overrides = params.get("property_codes")
    function_overrides = params.get("function_codes")

    inverter_properties = context.properties.get(inverter_device_id, {})
    pv_code = _resolve_property_code(
        inverter_device,
        "pv_power",
        property_overrides,
        inverter_properties,
    )
    load_properties = context.properties.get(load_device_id, {})
    switch_code = _resolve_function_code(load_device, "switch", function_overrides, load_properties)

    threshold = params.get("pv_threshold_w")
    if threshold is None:
        threshold = params.get("threshold")
    if threshold is None:
        threshold = params.get("status_value")
    if threshold is None:
        threshold = params.get("pv_threshold")
    if threshold is None:
        threshold = 800

    condition = _build_condition(
        entity_id=inverter_device_id,
        status_code=pv_code,
        comparator=params.get("comparator", "<"),
        status_value=threshold,
    )

    actions: List[SceneAction] = [
        _build_action(
            entity_id=load_device_id,
            function_code=switch_code,
            function_value=_coerce_bool(params.get("switch_value")) if params.get("switch_value") is not None else False,
        )
    ]
    actions.extend(_build_extra_actions(context, params.get("extra_actions"), function_overrides))

    return HeuristicProposal(
        key="solar_deficit",
        name=params.get("name", "Solar Deficit"),
        conditions=[condition],
        actions=actions,
        decision_expr=params.get("decision_expr", "and"),
    )


def heuristic_night_guard(context: HeuristicContext, params: Dict[str, Any]) -> HeuristicProposal:
    inverter_device_id = params.get("inverter_device_id")
    load_device_id = params.get("load_device_id")
    if not inverter_device_id or not load_device_id:
        raise ValueError("night_guard heuristic requires inverter_device_id and load_device_id")

    inverter_device = _ensure_device(context, inverter_device_id, "inverter")
    load_device = _ensure_device(context, load_device_id, "load")

    property_overrides = params.get("property_codes")
    function_overrides = params.get("function_codes")

    inverter_properties = context.properties.get(inverter_device_id, {})
    pv_code = _resolve_property_code(
        inverter_device,
        "pv_power",
        property_overrides,
        inverter_properties,
    )
    load_properties = context.properties.get(load_device_id, {})
    switch_code = _resolve_function_code(load_device, "switch", function_overrides, load_properties)

    loops = params.get("loops", "1111111")
    effective_time = EffectiveTime(
        start=params.get("start", "18:00"),
        end=params.get("end", "06:00"),
        loops=loops,
        time_zone_id=params.get("time_zone_id") or context.config.get("time_zone_id"),
    )

    conditions = [
        _build_condition(
            entity_id=inverter_device_id,
            status_code=pv_code,
            comparator=params.get("comparator", "=="),
            status_value=params.get("status_value", 0),
        )
    ]

    actions = [
        _build_action(
            entity_id=load_device_id,
            function_code=switch_code,
            function_value=params.get("switch_value", False),
        )
    ]

    return HeuristicProposal(
        key="night_guard",
        name=params.get("name", "Night Guard"),
        conditions=conditions,
        actions=actions,
        decision_expr=params.get("decision_expr", "and"),
        effective_time=effective_time,
    )


HEURISTIC_REGISTRY: Dict[str, Callable[[HeuristicContext, Dict[str, Any]], HeuristicProposal]] = {
    "battery_protect": heuristic_battery_protect,
    "battery_surplus": heuristic_battery_surplus,
    "solar_surplus": heuristic_solar_surplus,
    "night_guard": heuristic_night_guard,
    "solar_deficit": heuristic_solar_deficit,
}


def build_heuristic_proposals(
    context: HeuristicContext,
    keys: Iterable[str],
) -> List[HeuristicProposal]:
    proposals: List[HeuristicProposal] = []
    for key in keys:
        handler = HEURISTIC_REGISTRY.get(key)
        if not handler:
            raise ValueError(f"Unknown heuristic '{key}'")
        params = context.config.get("heuristics", {}).get(key, {})
        proposals.append(handler(context, params))
    return proposals
