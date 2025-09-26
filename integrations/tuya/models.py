"""Pydantic models describing Tuya Cloud entities used in automations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DeviceLite(BaseModel):
    """Minimal device representation returned by /cloud/thing/space/device."""

    id: str
    productId: Optional[str] = None
    category: Optional[str] = None
    name: Optional[str] = None
    isOnline: Optional[bool] = None
    customName: Optional[str] = None
    ip: Optional[str] = None
    model: Optional[str] = None
    time_zone: Optional[str] = Field(default=None, alias="timeZone")

    model_config = ConfigDict(populate_by_name=True, alias_generator=None, protected_namespaces=())


class Property(BaseModel):
    code: str
    value: Any
    time: Optional[int] = Field(default=None, alias="time")
    custom_name: Optional[str] = Field(default=None, alias="custom_name")
    dp_id: Optional[int] = Field(default=None, alias="dp_id")
    type: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())


class ConditionExpr(BaseModel):
    status_code: Optional[str] = None
    comparator: Optional[str] = None
    status_value: Any = None
    date: Optional[str] = None
    time: Optional[str] = None
    loops: Optional[str] = None
    time_zone_id: Optional[str] = None
    weather_code: Optional[str] = None
    weather_value: Any = None


class SceneCondition(BaseModel):
    entity_id: str
    entity_type: str
    expr: ConditionExpr
    code: Optional[int] = None


class ExecutorProperty(BaseModel):
    function_code: str
    function_value: Any


class SceneAction(BaseModel):
    entity_id: str
    action_executor: str
    executor_property: ExecutorProperty


class EffectiveTime(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    loops: Optional[str] = None
    time_zone_id: Optional[str] = None


class SceneRule(BaseModel):
    space_id: str
    name: str
    type: str
    decision_expr: str
    conditions: List[SceneCondition]
    actions: List[SceneAction]
    effective_time: Optional[EffectiveTime] = None

    def as_payload(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)
