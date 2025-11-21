from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google import genai
from google.genai import types

import core.goodweApi as goodweApi
from core import usage_optimizer
from core.tuya_scene_builder import prewarm_scene_builder
from integrations.tuya.ai_tools import (
    build_scene_payload_from_instructions,
    create_and_enable_automation,
    delete_automations,
    describe_space,
    inspect_device,
    prewarm_tuya_caches,
    propose_automation,
    set_automation_state,
    trigger_scene,
    update_automation,
)

load_dotenv(".env")

_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_MAX_GEMINI_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
_BASE_RETRY_DELAY_SECONDS = float(os.getenv("GEMINI_RETRY_BASE_DELAY", "1.0"))
_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

DEFAULT_STATION_NAME = os.getenv("DEFAULT_STATION_NAME", "").strip()
DEFAULT_STATION_ID = os.getenv("DEFAULT_STATION_ID", "").strip()
DEFAULT_TUYA_SPACE_ID = os.getenv("TUYA_SPACE_ID", "").strip()

_TUYA_CACHE_TTL_SECONDS = float(os.getenv("TUYA_CONTEXT_CACHE_TTL", "300.0"))
_TUYA_BOOTSTRAP_TIMEOUT_SECONDS = float(os.getenv("TUYA_DESCRIBE_TIMEOUT", "3.0"))

_AUTOMATION_KEYWORDS: Sequence[str] = (
    "automation",
    "automacao",
    "automação",
    "scene",
    "cena",
    "smart plug",
    "smartplug",
    "tomada",
    "tuya",
    "device",
    "dispositivo",
    "light",
    "luz",
    "ligar",
    "desligar",
    "home automation",
    "tap-to-run",
    "tap to run",
)


def _extract_status_code(error: Exception) -> Optional[int]:
    if hasattr(error, "code"):
        code = getattr(error, "code")
        if isinstance(code, int):
            return code
        if isinstance(code, str) and code.isdigit():
            return int(code)
    if hasattr(error, "status_code"):
        status_code = getattr(error, "status_code")
        if isinstance(status_code, int):
            return status_code
    if hasattr(error, "response"):
        response = getattr(error, "response")
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    message = str(error)
    match = re.search(r"\b(\d{3})\b", message)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _is_retryable_gemini_error(error: Exception) -> bool:
    status_code = _extract_status_code(error)
    if status_code in _RETRYABLE_STATUS_CODES:
        return True
    message = str(error).upper()
    retryable_tokens = (
        "UNAVAILABLE",
        "MODEL IS OVERLOADED",
        "TRY AGAIN LATER",
        "RATE_LIMIT",
        "OVERLOADED",
        "TIMEOUT",
    )
    return any(token in message for token in retryable_tokens)


def _normalize_label(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    return normalized


def _build_lookup(entries: Sequence[Dict[str, Any]], *, name_fields: Sequence[str]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for entry in entries or []:
        identifier = entry.get("id") or entry.get("rule_id")
        if not identifier:
            continue
        seen: set[str] = set()
        for field in name_fields:
            candidate = entry.get(field)
            if not candidate:
                continue
            label = str(candidate).strip()
            if not label:
                continue
            normalized = _normalize_label(label)
            if not normalized or normalized in seen:
                continue
            lookup[normalized] = identifier
            seen.add(normalized)
    return lookup


@dataclass
class TuyaContextCache:
    space_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    timestamp: float = 0.0
    device_lookup: Dict[str, str] = field(default_factory=dict)
    scene_lookup: Dict[str, str] = field(default_factory=dict)


class TuyaContextManager:
    def __init__(
        self,
        default_space_id: Optional[str],
        *,
        cache_ttl: float = _TUYA_CACHE_TTL_SECONDS,
        bootstrap_timeout: float = _TUYA_BOOTSTRAP_TIMEOUT_SECONDS,
    ) -> None:
        self.default_space_id = default_space_id or ""
        self.cache_ttl = cache_ttl
        self.bootstrap_timeout = bootstrap_timeout
        self._cache = TuyaContextCache()

    def invalidate(self) -> None:
        self._cache = TuyaContextCache()

    def _update_cache(self, space_id: str, payload: Dict[str, Any]) -> None:
        devices = payload.get("devices") or []
        scenes = payload.get("scenes") or []
        self._cache = TuyaContextCache(
            space_id=space_id,
            payload=payload,
            timestamp=time.time(),
            device_lookup=_build_lookup(devices, name_fields=("customName", "name")),
            scene_lookup=_build_lookup(scenes, name_fields=("customName", "name", "display_name")),
        )

    def _is_valid(self, space_id: str) -> bool:
        if not self._cache.payload or self._cache.space_id != space_id:
            return False
        return (time.time() - self._cache.timestamp) < self.cache_ttl

    def get_cached_payload(self, space_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not space_id:
            return None
        if self._is_valid(space_id):
            return self._cache.payload
        return None

    def _format_context(self, payload: Dict[str, Any]) -> str:
        space_id = payload.get("space_id") or self.default_space_id or ""
        devices = payload.get("devices") or []
        scenes = payload.get("scenes") or []

        device_lines = []
        for device in devices:
            friendly = (device.get("customName") or device.get("name") or "Device").strip()
            device_id = device.get("id", "")
            device_lines.append(f"- {friendly} -> {device_id}")
        if not device_lines:
            device_lines.append("- no devices available")

        scene_lines = []
        for scene in scenes:
            friendly = (scene.get("name") or scene.get("display_name") or "Scene").strip()
            rule_id = scene.get("rule_id") or scene.get("id") or ""
            scene_lines.append(f"- {friendly} -> {rule_id}")
        if not scene_lines:
            scene_lines.append("- no scenes registered")

        return "\n".join(
            [
                "[Internal Tuya context - never expose IDs]",
                f"space_id (internal): {space_id}",
                "Known devices:",
                *device_lines,
                "Known scenes:",
                *scene_lines,
                "Use only friendly names in responses; use IDs only inside function calls.",
            ]
        )

    def _resolve_identifier(self, identifier: Any, lookup: Dict[str, str]) -> Any:
        if not isinstance(identifier, str):
            return identifier
        trimmed = identifier.strip()
        if not trimmed:
            return identifier
        normalized = _normalize_label(trimmed)
        resolved = lookup.get(normalized)
        if resolved:
            return resolved
        if trimmed in lookup.values():
            return trimmed
        return identifier

    def resolve_device_identifier(self, identifier: Any) -> Any:
        return self._resolve_identifier(identifier, self._cache.device_lookup)

    def resolve_scene_identifier(self, identifier: Any) -> Any:
        return self._resolve_identifier(identifier, self._cache.scene_lookup)

    def _should_bootstrap(self, message: str) -> bool:
        if not message:
            return False
        lower = message.lower()
        return any(keyword in lower for keyword in _AUTOMATION_KEYWORDS)

    async def refresh(self, space_id: Optional[str]) -> Optional[Dict[str, Any]]:
        target = space_id or self.default_space_id
        if not target:
            return None
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(describe_space, target),
                timeout=self.bootstrap_timeout,
            )
            if isinstance(payload, dict):
                self._update_cache(target, payload)
                return payload
        except asyncio.TimeoutError:
            print(f"⚠️ Tuya describe_space timed out after {self.bootstrap_timeout}s for space {target}.")
        except Exception as exc:  # pragma: no cover - network failure
            print(f"⚠️ Could not refresh Tuya context: {exc}")
        return self._cache.payload if self._cache.payload else None

    async def ensure_context(self, space_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        target = space_id or self.default_space_id
        if not target:
            return None
        if self._is_valid(target):
            return self._cache.payload

        # If cache exists but is stale, use it immediately and refresh in the background.
        if self._cache.payload and self._cache.space_id == target:
            asyncio.create_task(self.refresh(target))
            return self._cache.payload

        return await self.refresh(target)

    async def augment_user_input(self, user_input: str) -> tuple[str, bool]:
        if not self._should_bootstrap(user_input):
            return user_input, False
        cached = await self.ensure_context(self.default_space_id)
        if not cached:
            return user_input, False
        context_text = self._format_context(cached)
        augmented = f"{context_text}\n\nUser: {user_input}"
        return augmented, True


def _auto_date_range(args: Dict[str, Any]) -> Dict[str, Any]:
    tz = ZoneInfo("America/Sao_Paulo")
    today_dt = datetime.now(tz).date()

    sd_raw = (args.get("start_date") or "").strip() if args.get("start_date") else ""
    ed_raw = (args.get("end_date") or "").strip() if args.get("end_date") else ""

    import re as _re
    from datetime import datetime as _dt, timedelta as _td

    def _try_parse_iso(s: str):
        try:
            return _dt.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    def _try_parse_br(s: str):
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                return _dt.strptime(s, fmt).date()
            except Exception:
                continue
        return None

    def _parse_relative(s: str):
        if not s:
            return None
        s_l = s.lower().strip()
        if s_l in {"today", "hoje"}:
            return today_dt
        if s_l in {"ontem", "yesterday"}:
            return today_dt - _td(days=1)
        m = _re.match(r"^(hoje|today)-(\d+)$", s_l)
        if m:
            return today_dt - _td(days=int(m.group(2)))
        m2 = _re.match(r"^(\d{4}-\d{2}-\d{2})-(\d+)$", s_l)
        if m2:
            base = _try_parse_iso(m2.group(1))
            if base:
                return base - _td(days=int(m2.group(2)))
        return None

    def _month_bounds(d):
        first = d.replace(day=1)
        next_month = (first.replace(day=28) + _td(days=4)).replace(day=1)
        last = next_month - _td(days=1)
        return first, last

    sd_dt = ed_dt = None

    sd_lower = (sd_raw or "").lower()
    if not sd_raw and not ed_raw:
        sd_dt = today_dt.replace(month=1, day=1)
        ed_dt = today_dt
    elif sd_lower in {"este ano", "ano atual", "this year"}:
        sd_dt = today_dt.replace(month=1, day=1)
        ed_dt = today_dt if not ed_raw else None
    elif sd_lower in {"este mes", "este mês", "mes atual", "mês atual", "this month"}:
        first, _ = _month_bounds(today_dt)
        sd_dt = first
        ed_dt = today_dt if not ed_raw else None
    elif sd_lower in {"mes passado", "mês passado", "last month"}:
        first_this, _ = _month_bounds(today_dt)
        last_prev = first_this - _td(days=1)
        first_prev, last_prev_b = _month_bounds(last_prev)
        sd_dt = first_prev
        ed_dt = last_prev_b
    else:
        sd_dt = _try_parse_iso(sd_raw) or _try_parse_br(sd_raw) or _parse_relative(sd_raw)

    if ed_dt is None:
        ed_lower = (ed_raw or "").lower()
        if not ed_raw:
            ed_dt = sd_dt or today_dt
        elif ed_lower in {"today", "hoje"}:
            ed_dt = today_dt
        elif ed_lower in {"ontem", "yesterday"}:
            ed_dt = today_dt - _td(days=1)
        else:
            ed_dt = _try_parse_iso(ed_raw) or _try_parse_br(ed_raw) or _parse_relative(ed_raw)

    m_last = _re.search(r"\b(?:ultimos|últimos|last)\s+(\d+)\s+dias\b", sd_lower)
    if m_last:
        n = int(m_last.group(1))
        ed_dt = today_dt
        sd_dt = today_dt - _td(days=max(0, n - 1))

    sd_dt = sd_dt or today_dt
    ed_dt = ed_dt or sd_dt

    if sd_dt > ed_dt:
        sd_dt, ed_dt = ed_dt, sd_dt

    args["start_date"] = sd_dt.isoformat()
    args["end_date"] = ed_dt.isoformat()
    return args


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {k: _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)


def get_system_prompt() -> str:
    try:
        with open("system_prompt.txt", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "You are BotSolar, a GoodWe and Tuya assistant."


def _today_payload() -> Dict[str, str]:
    tz = ZoneInfo("America/Sao_Paulo")
    now = datetime.now(tz)
    return {"today": now.date().isoformat()}


def create_function_declarations():
    functions = []

    functions.append(
        types.FunctionDeclaration(
            name="list_plants",
            description="List all GoodWe plants available for the authenticated account.",
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="get_powerstation_battery_status",
            description="Return battery status for a powerstation. If powerstation_id is missing, use the configured default plant.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "powerstation_id": types.Schema(
                        type=types.Type.STRING,
                        description="Optional. GoodWe powerstation id; defaults to the configured plant.",
                    )
                },
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="get_today_date",
            description="Return today's date in ISO format (America/Sao_Paulo).",
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="get_alarms_by_range",
            description="Return alarms for a date/range. Optionally filter by station name in the presentation layer.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "start_date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD , also accepts 'today'/'hoje' or 'yesterday'/'ontem'"),
                    "end_date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD (optional), also accepts 'today'/'hoje' or 'yesterday'/'ontem'"),
                    "status": types.Schema(type=types.Type.STRING, description='"0"=Active, "1"=History, "3"=All'),
                    "stationname": types.Schema(type=types.Type.STRING, description="Optional case-insensitive station name filter"),
                },
                required=["start_date"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="get_warning_detail",
            description="Get human-readable detail for a specific warning (stationid, warningid, devicesn).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "stationid": types.Schema(type=types.Type.STRING),
                    "warningid": types.Schema(type=types.Type.STRING),
                    "devicesn": types.Schema(type=types.Type.STRING),
                },
                required=["stationid", "warningid", "devicesn"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="get_powerstation_power_and_income_by_day",
            description="Get daily energy generation and income. 'd' is date, 'p' power, 'i' income in USD.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "powerstation_id": types.Schema(
                        type=types.Type.STRING,
                        description="Powerstation ID. Fetch via list_plants when only the name is provided.",
                    ),
                    "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD , also accepts 'today'/'hoje' or 'yesterday'/'ontem'"),
                    "count": types.Schema(type=types.Type.INTEGER, description="Number of days to retrieve (1=current date, 2=current+previous, etc.)"),
                },
                required=["date"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="get_powerstation_power_and_income_by_month",
            description="Get monthly energy generation and income in USD.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "powerstation_id": types.Schema(
                        type=types.Type.STRING,
                        description="Powerstation ID. Fetch via list_plants when only the name is provided.",
                    ),
                    "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD; use 'today' for the current month."),
                    "count": types.Schema(type=types.Type.INTEGER, description="Number of months to retrieve."),
                },
                required=["date"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="get_powerstation_power_and_income_by_year",
            description="Get yearly energy generation and income in USD.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "powerstation_id": types.Schema(
                        type=types.Type.STRING,
                        description="Powerstation ID. Fetch via list_plants when only the name is provided.",
                    ),
                    "date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD; use 'today' for the current year."),
                    "count": types.Schema(type=types.Type.INTEGER, description="Number of years to retrieve."),
                },
                required=["date"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="get_ev_charger_status",
            description="Return EV charger status for a powerstation_id. Charge Mode 1 = Fast, 2 = PV Priority, 3 = PV & Battery.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "powerstation_id": types.Schema(
                        type=types.Type.STRING,
                        description="ID of the station associated with the EV charger. Defaults to configured plant.",
                    )
                },
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="change_ev_charger_status",
            description="Change the EV charger mode for a powerstation_id.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "powerstation_id": types.Schema(
                        type=types.Type.STRING,
                        description="ID of the station associated with the EV charger. Defaults to configured plant.",
                    ),
                    "charge_mode": types.Schema(
                        type=types.Type.INTEGER,
                        description="Charge_mode 1 - Fast, 2 - PV Priority, 3 - PV & Battery.",
                    ),
                },
                required=["charge_mode"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="optimize_usage",
            description="Generate a short statistical report from the last 7 days of minute-level history for optimization tips.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "parsed_path": types.Schema(
                        type=types.Type.STRING,
                        description="Optional path to a history7d_parsed_*.json file. Uses latest if omitted.",
                    )
                },
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_describe_space",
            description="List Tuya devices and scenes for a space (safe for sharing names only).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "space_id": types.Schema(type=types.Type.STRING, description="Optional: Tuya space ID (defaults to configured space)."),
                    "config_path": types.Schema(type=types.Type.STRING, description="Optional: custom configs/automation.yaml path."),
                },
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_inspect_device",
            description="Fetch datapoints for a Tuya device to explain codes and values.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "device_id": types.Schema(type=types.Type.STRING, description="Tuya device ID."),
                    "codes": types.Schema(
                        type=types.Type.ARRAY,
                        description="Optional list of DP codes to filter.",
                        items=types.Schema(type=types.Type.STRING),
                    ),
                },
                required=["device_id"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_propose_automation",
            description="Generate Tuya automation payloads using heuristics (preview only; does not create rules).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "space_id": types.Schema(type=types.Type.STRING, description="Optional: Tuya space ID (defaults to configured space)."),
                    "heuristic_set": types.Schema(
                        type=types.Type.ARRAY,
                        description="Optional subset of heuristics (battery_protect, battery_surplus, solar_surplus, night_guard).",
                        items=types.Schema(type=types.Type.STRING),
                    ),
                    "config_path": types.Schema(type=types.Type.STRING, description="Optional: alternative automation.yaml path."),
                    "heuristic_overrides": types.Schema(
                        type=types.Type.OBJECT,
                        description="Optional heuristic overrides (e.g., inverter_device_id, load_device_id, threshold).",
                    ),
                },
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_build_scene_payload",
            description="Generate and create a Tuya automation or tap-to-run from natural language instructions. Returns the created automation details.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "instructions": types.Schema(
                        type=types.Type.STRING,
                        description="Desired automation description including condition(s) and action(s).",
                    ),
                    "space_id": types.Schema(
                        type=types.Type.STRING,
                        description="Optional Tuya space ID (uses default if absent).",
                    ),
                    "device_ids": types.Schema(
                        type=types.Type.ARRAY,
                        description="Optional list of device IDs referenced in the instructions to filter context.",
                        items=types.Schema(type=types.Type.STRING),
                    ),
                    "name_hint": types.Schema(
                        type=types.Type.STRING,
                        description="Optional suggested name for the new automation.",
                    ),
                    "decision_expr_hint": types.Schema(
                        type=types.Type.STRING,
                        description="Optional boolean expression (e.g., 'and', 'or', 'c1&c2').",
                    ),
                    "effective_time_hint": types.Schema(
                        type=types.Type.OBJECT,
                        description="Optional time window including start/end/loops/time_zone_id.",
                    ),
                    "type_hint": types.Schema(
                        type=types.Type.STRING,
                        description="Optional rule type ('automation' or 'scene').",
                    ),
                },
                required=["instructions"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_create_and_enable_automation",
            description="Create (and optionally enable) a Tuya scene. Requires explicit confirmation.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "payload": types.Schema(type=types.Type.OBJECT, description="Full scene payload following Tuya templates."),
                    "confirm": types.Schema(type=types.Type.BOOLEAN, description="Must be true after the user authorizes creation."),
                    "enable": types.Schema(type=types.Type.BOOLEAN, description="If true, enable the scene after creation."),
                },
                required=["payload", "confirm"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_update_automation",
            description="Update an existing Tuya scene with a new payload (confirmation required).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "rule_id": types.Schema(type=types.Type.STRING, description="ID of the scene/rule to update."),
                    "payload": types.Schema(type=types.Type.OBJECT, description="Updated payload following the Tuya schema."),
                    "confirm": types.Schema(type=types.Type.BOOLEAN, description="Must be true after the user approves the change."),
                },
                required=["rule_id", "payload", "confirm"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_delete_automations",
            description="Delete one or more Tuya scenes (confirmation required).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "rule_ids": types.Schema(
                        type=types.Type.ARRAY,
                        description="List of scene IDs to remove.",
                        items=types.Schema(type=types.Type.STRING),
                    ),
                    "space_id": types.Schema(type=types.Type.STRING, description="Optional: space_id to scope the deletion."),
                    "config_path": types.Schema(type=types.Type.STRING, description="Optional: alternative automation.yaml path."),
                    "confirm": types.Schema(type=types.Type.BOOLEAN, description="Must be true after the user requests deletion."),
                },
                required=["rule_ids", "confirm"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_set_automation_state",
            description="Enable or disable a list of Tuya scenes (confirmation required).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "rule_ids": types.Schema(
                        type=types.Type.ARRAY,
                        description="List of scene IDs to change state.",
                        items=types.Schema(type=types.Type.STRING),
                    ),
                    "enable": types.Schema(type=types.Type.BOOLEAN, description="True to enable, False to disable."),
                    "confirm": types.Schema(type=types.Type.BOOLEAN, description="Must be true after user confirmation."),
                },
                required=["rule_ids", "enable", "confirm"],
            ),
        )
    )

    functions.append(
        types.FunctionDeclaration(
            name="tuya_trigger_scene",
            description="Trigger a Tuya scene manually (confirmation required).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "rule_id": types.Schema(type=types.Type.STRING, description="ID of the scene to trigger."),
                    "confirm": types.Schema(type=types.Type.BOOLEAN, description="Must be true after the user requests execution."),
                },
                required=["rule_id", "confirm"],
            ),
        )
    )
    return functions


class FunctionDispatcher:
    def __init__(self, tuya_context: TuyaContextManager) -> None:
        self.goodwe_api_instance = goodweApi.GoodweApi()
        self.tuya_context = tuya_context

    def _get_default_powerstation_id(self) -> str:
        if DEFAULT_STATION_ID:
            return DEFAULT_STATION_ID
        try:
            plants = self.goodwe_api_instance.ListPlants() or {}
            plant_list = plants.get("plants", []) if isinstance(plants, dict) else []
            for p in plant_list:
                if (p.get("stationname") or "").strip().lower() == DEFAULT_STATION_NAME.strip().lower():
                    return p.get("powerstation_id") or ""
            return (plant_list[0].get("powerstation_id") if plant_list else "") or ""
        except Exception:
            return ""

    def _prepare_alarm_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        args = _auto_date_range(dict(args))
        stationname = args.get("stationname") or DEFAULT_STATION_NAME
        if stationname:
            args["stationname"] = stationname
            args["searchKey"] = stationname
        if not args.get("status"):
            tz = ZoneInfo("America/Sao_Paulo")
            today = datetime.now(tz).date()
            try:
                sd = datetime.fromisoformat(args.get("start_date")).date()
                ed = datetime.fromisoformat(args.get("end_date") or args.get("start_date")).date()
            except Exception:
                sd = ed = today
            if sd == today and ed == today:
                args["status"] = "0"
            else:
                args["status"] = "3"
        return args

    def _resolve_tuya_args(self, function_name: str, function_args: Dict[str, Any]) -> Dict[str, Any]:
        args = dict(function_args)
        if function_name == "tuya_describe_space":
            args["space_id"] = args.get("space_id") or self.tuya_context.default_space_id
        if function_name == "tuya_propose_automation":
            args["space_id"] = args.get("space_id") or self.tuya_context.default_space_id
            overrides = args.get("heuristic_overrides") or {}
            if isinstance(overrides, dict):
                resolved_overrides: Dict[str, Any] = {}
                for key, params in overrides.items():
                    if isinstance(params, dict):
                        resolved_params = dict(params)
                        for override_key in ("inverter_device_id", "load_device_id", "sensor_device_id"):
                            resolved_params[override_key] = self.tuya_context.resolve_device_identifier(
                                resolved_params.get(override_key)
                            )
                        resolved_overrides[key] = resolved_params
                    else:
                        resolved_overrides[key] = params
                args["heuristic_overrides"] = resolved_overrides
        if function_name in {"tuya_delete_automations", "tuya_set_automation_state"}:
            rule_ids = args.get("rule_ids")
            if isinstance(rule_ids, Sequence) and not isinstance(rule_ids, (str, bytes)):
                resolved_rule_ids = []
                for item in rule_ids:
                    resolved = self.tuya_context.resolve_scene_identifier(item)
                    if resolved:
                        resolved_rule_ids.append(resolved)
                args["rule_ids"] = resolved_rule_ids
        if function_name in {"tuya_update_automation", "tuya_trigger_scene"} and "rule_id" in args:
            args["rule_id"] = self.tuya_context.resolve_scene_identifier(args.get("rule_id"))
        if function_name in {"tuya_describe_space", "tuya_propose_automation", "tuya_build_scene_payload"} and not args.get("space_id"):
            args["space_id"] = self.tuya_context.default_space_id
        if function_name == "tuya_build_scene_payload":
            device_ids = args.get("device_ids")
            if isinstance(device_ids, list):
                resolved_ids = []
                for item in device_ids:
                    resolved = self.tuya_context.resolve_device_identifier(item)
                    if resolved:
                        resolved_ids.append(resolved)
                args["device_ids"] = resolved_ids

        # Default confirmations to True to avoid extra turns when the user already requested the change.
        if function_name in {
            "tuya_create_and_enable_automation",
            "tuya_update_automation",
            "tuya_delete_automations",
            "tuya_set_automation_state",
            "tuya_trigger_scene",
        }:
            if "confirm" not in args:
                args["confirm"] = True
        if function_name == "tuya_create_and_enable_automation" and "enable" not in args:
            args["enable"] = True
        return args

    def execute(self, function_call, *, powerstation_override: Optional[str] = None):
        function_map = {
            "list_plants": self.goodwe_api_instance.ListPlants,
            "get_powerstation_battery_status": self.goodwe_api_instance.GetSoc,
            "get_alarms_by_range": self.goodwe_api_instance.GetAlarmsByRange,
            "get_warning_detail": self.goodwe_api_instance.GetWarningDetailTranslated,
            "get_powerstation_power_and_income_by_day": self.goodwe_api_instance.GetPowerAndIncomeByDay,
            "get_powerstation_power_and_income_by_month": self.goodwe_api_instance.GetPowerAndIncomeByMonth,
            "get_powerstation_power_and_income_by_year": self.goodwe_api_instance.GetPowerAndIncomeByYear,
            "optimize_usage": usage_optimizer.optimize_usage,
            "get_ev_charger_status": self.goodwe_api_instance.GetEvChargerChargingMode,
            "change_ev_charger_status": self.goodwe_api_instance.ChangeEvChargerChargingMode,
            "tuya_describe_space": describe_space,
            "tuya_inspect_device": inspect_device,
            "tuya_propose_automation": propose_automation,
            "tuya_build_scene_payload": build_scene_payload_from_instructions,
            "tuya_create_and_enable_automation": create_and_enable_automation,
            "tuya_update_automation": update_automation,
            "tuya_delete_automations": delete_automations,
            "tuya_set_automation_state": set_automation_state,
            "tuya_trigger_scene": trigger_scene,
            "get_today_date": _today_payload,
        }

        needs_powerstation = {
            "get_powerstation_battery_status",
            "get_powerstation_power_and_income_by_day",
            "get_powerstation_power_and_income_by_month",
            "get_powerstation_power_and_income_by_year",
            "get_ev_charger_status",
            "change_ev_charger_status",
        }

        function_name = function_call.name
        function_args = dict(function_call.args) if function_call.args else {}
        fallback_to_default = False
        used_powerstation_id = function_args.get("powerstation_id")
        started_at = time.perf_counter()

        if function_name.startswith("tuya_"):
            function_args = self._resolve_tuya_args(function_name, function_args)

        if function_name == "tuya_describe_space":
            cached_payload = self.tuya_context.get_cached_payload(function_args.get("space_id"))
            if cached_payload and not function_args.get("config_path"):
                meta = {
                    "fallback_to_default": fallback_to_default,
                    "used_powerstation_id": used_powerstation_id,
                    "args_preview": _json_safe(function_args),
                    "result_preview": _json_safe(cached_payload),
                    "from_cache": True,
                }
                return cached_payload, meta["args_preview"], meta["result_preview"], meta

        if function_name in function_map:
            try:
                if function_name == "get_alarms_by_range":
                    function_args = self._prepare_alarm_args(function_args)

                if function_name in needs_powerstation:
                    if function_args.get("powerstation_id"):
                        used_powerstation_id = function_args["powerstation_id"]
                    elif powerstation_override:
                        function_args["powerstation_id"] = powerstation_override
                        used_powerstation_id = powerstation_override
                    else:
                        default_station = self._get_default_powerstation_id()
                        if default_station:
                            function_args["powerstation_id"] = default_station
                            used_powerstation_id = default_station
                            fallback_to_default = True

                if function_name == "tuya_build_scene_payload":
                    build_result = function_map[function_name](**function_args)
                    result = build_result
                    if isinstance(build_result, dict):
                        payload = build_result.get("payload") or {}
                        if payload:
                            try:
                                create_result = create_and_enable_automation(
                                    payload=payload,
                                    confirm=True,
                                    enable=True,
                                )
                                result = {"payload": build_result, "created": create_result}
                                self.tuya_context.invalidate()
                            except Exception as exc:
                                result = {"payload": build_result, "create_error": str(exc)}
                elif function_name == "get_today_date":
                    result = function_map[function_name]()
                else:
                    result = function_map[function_name](**function_args)

                duration = time.perf_counter() - started_at

                if function_name == "tuya_describe_space":
                    target_space = function_args.get("space_id") or self.tuya_context.default_space_id
                    if isinstance(result, dict):
                        self.tuya_context._update_cache(target_space, result)
                elif function_name in {
                    "tuya_create_and_enable_automation",
                    "tuya_update_automation",
                    "tuya_delete_automations",
                    "tuya_set_automation_state",
                }:
                    self.tuya_context.invalidate()

                preview_args = _json_safe(function_args)
                preview_result = _json_safe(result)

                meta = {
                    "fallback_to_default": fallback_to_default,
                    "used_powerstation_id": used_powerstation_id,
                    "args_preview": preview_args,
                    "result_preview": preview_result,
                    "duration_s": duration,
                }
                return result, preview_args, preview_result, meta
            except Exception as exc:
                print(f"❌ Error executing function '{function_name}': {exc}")
                error_payload = {"error": str(exc)}
                meta = {
                    "fallback_to_default": fallback_to_default,
                    "used_powerstation_id": used_powerstation_id,
                    "args_preview": _json_safe(function_args),
                    "result_preview": _json_safe(error_payload),
                    "duration_s": time.perf_counter() - started_at,
                }
                return error_payload, meta["args_preview"], meta["result_preview"], meta

        error_payload = {"error": f"Unknown function: {function_name}"}
        meta = {
            "fallback_to_default": fallback_to_default,
            "used_powerstation_id": used_powerstation_id,
            "args_preview": _json_safe(function_args),
            "result_preview": _json_safe(error_payload),
            "duration_s": time.perf_counter() - started_at,
        }
        return error_payload, meta["args_preview"], meta["result_preview"], meta


class GeminiAssistant:
    def __init__(self) -> None:
        self.client: Optional[genai.Client] = None
        self.chat_instance: Optional[Any] = None
        self.tuya_context = TuyaContextManager(DEFAULT_TUYA_SPACE_ID)
        self.dispatcher = FunctionDispatcher(self.tuya_context)

    def _warmup_background(self) -> None:
        if DEFAULT_TUYA_SPACE_ID:
            threading.Thread(target=prewarm_tuya_caches, args=(DEFAULT_TUYA_SPACE_ID,), daemon=True).start()
        threading.Thread(target=prewarm_scene_builder, daemon=True).start()

    def initialize_chat(self) -> bool:
        try:
            self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            self.chat_instance = self.client.chats.create(
                model=_GEMINI_MODEL,
                config=types.GenerateContentConfig(
                    system_instruction=get_system_prompt(),
                    tools=[types.Tool(function_declarations=create_function_declarations())],
                ),
            )
            self._warmup_background()
            return True
        except Exception as exc:
            print(f"❌ Error initializing chat: {exc}")
            return False

    async def _send_with_retry(self, message: Any, *, allow_recreate: bool = False) -> Any:
        last_error: Optional[Exception] = None
        if self.chat_instance is None:
            raise RuntimeError("Gemini chat instance is not initialised.")

        for attempt in range(1, _MAX_GEMINI_RETRIES + 1):
            try:
                return self.chat_instance.send_message(message=message)
            except Exception as error:  # pragma: no cover - network failure
                last_error = error
                if attempt >= _MAX_GEMINI_RETRIES or not _is_retryable_gemini_error(error):
                    raise
                delay = _BASE_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                print(
                    f"⚠️ Gemini call failed (attempt {attempt}/{_MAX_GEMINI_RETRIES}) with retryable error: {error}. "
                    f"Retrying in {delay:.1f}s."
                )
                await asyncio.sleep(delay)
                if allow_recreate:
                    if not self.initialize_chat():
                        break

        if last_error:
            raise last_error
        raise RuntimeError("Gemini request failed without raising an exception.")

    async def ask(self, user_input: str, *, powerstation_id: Optional[str] = None) -> Dict[str, Any]:
        if self.chat_instance is None:
            if not self.initialize_chat():
                return {
                    "response": "❌ Error: Could not initialize the chat system.",
                    "functions_preview": [],
                    "fallback_to_default": False,
                    "used_powerstation_id": powerstation_id,
                    "timings": {"steps": [], "functions": []},
                }

        try:
            augmented_input, _ = await self.tuya_context.augment_user_input(user_input)
            trace_steps = []
            t0 = time.perf_counter()
            response = await self._send_with_retry(augmented_input)
            trace_steps.append({"step": "gemini_initial", "duration_s": time.perf_counter() - t0})
            function_executed = False
            executed_functions = []
            final_answer_chunks = []
            used_powerstation_id = powerstation_id
            fallback_to_default = False
            followup_idx = 0

            while True:
                function_response_parts = []
                has_function_call = False

                if hasattr(response, "candidates") and response.candidates:
                    candidate = response.candidates[0]
                    content = getattr(candidate, "content", None)
                    parts = getattr(content, "parts", []) if content else []

                    for part in parts:
                        if hasattr(part, "function_call") and part.function_call:
                            (
                                result,
                                preview_args,
                                preview_result,
                                meta,
                            ) = self.dispatcher.execute(part.function_call, powerstation_override=powerstation_id)

                            function_response_part = types.Part.from_function_response(
                                name=part.function_call.name,
                                response=result,
                            )
                            function_response_parts.append(function_response_part)
                            executed_functions.append(
                                {
                                    "name": part.function_call.name,
                                    "args": preview_args,
                                    "result": preview_result,
                                    "duration_s": meta.get("duration_s"),
                                }
                            )
                            if meta.get("used_powerstation_id"):
                                used_powerstation_id = meta.get("used_powerstation_id")
                            if meta.get("fallback_to_default"):
                                fallback_to_default = True
                            function_executed = True
                            has_function_call = True
                        elif hasattr(part, "text") and part.text:
                            final_answer_chunks.append(part.text)

                if has_function_call and function_response_parts:
                    t_step = time.perf_counter()
                    response = await self._send_with_retry(function_response_parts, allow_recreate=True)
                    followup_idx += 1
                    trace_steps.append(
                        {
                            "step": f"gemini_followup_{followup_idx}",
                            "duration_s": time.perf_counter() - t_step,
                        }
                    )
                else:
                    break

            response_text = getattr(response, "text", "") or ""
            final_answer = "\n".join(chunk.strip() for chunk in final_answer_chunks if chunk).strip()
            if not final_answer:
                final_answer = response_text.strip()
            elif response_text.strip() and response_text.strip() not in {
                chunk.strip() for chunk in final_answer_chunks if chunk
            }:
                final_answer = "\n".join(filter(None, [final_answer, response_text.strip()]))

            if not final_answer:
                final_answer = "Functions executed successfully." if function_executed else "Request processed."

            return {
                "response": final_answer,
                "functions_preview": executed_functions,
                "fallback_to_default": fallback_to_default,
                "used_powerstation_id": used_powerstation_id,
                "timings": {"steps": trace_steps, "functions": executed_functions},
            }
        except Exception as exc:
            print(f"❌ Error in call_geminiapi: {exc}")
            return {
                "response": f"❌ Error processing your request: {str(exc)}",
                "functions_preview": [],
                "fallback_to_default": False,
                "used_powerstation_id": powerstation_id,
                "timings": {"steps": [], "functions": []},
            }


_assistant = GeminiAssistant()


def initialize_chat():
    return _assistant.initialize_chat()


async def call_geminiapi(user_input: str, *, powerstation_id: Optional[str] = None) -> Dict[str, Any]:
    return await _assistant.ask(user_input, powerstation_id=powerstation_id)
